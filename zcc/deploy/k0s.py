"""k0s installer — installs and bootstraps k0s on cluster nodes."""

from __future__ import annotations

import logging
import os
import shlex
import time
from typing import TYPE_CHECKING

from .backend import ClusterBackend
from .ssh import SSHClient, SSHError

if TYPE_CHECKING:
    from ..models.backend import BackendConfig
    from ..models.host import Host

logger = logging.getLogger(__name__)

# k0s upstream installer script
_K0S_INSTALL_SCRIPT = "curl -sSLf https://get.k0s.sh | sudo sh"

# k0s service commands
_K0S_START = "sudo k0s start"
_K0S_STATUS = "sudo k0s status"
_CONTROLLER_TOKEN_PATH = "/tmp/k0s-controller-token"
_WORKER_TOKEN_PATH = "/tmp/k0s-worker-token"

# Well-known k0s configuration file path.  When present on the remote node,
# this file is passed to every `k0s install` invocation so that cluster-level
# settings (e.g. kube-router overlay mode) are picked up automatically.
_K0S_CONFIG_PATH = "/etc/k0s/k0s.yaml"

# How long (seconds) to wait for the k0s API server to become ready after start.
_K0S_READY_TIMEOUT = 120

# Mapping of logical BackendConfig.config_files key → remote destination path.
# Keys not listed here are silently ignored by K0sInstaller (other backends
# may recognise them as stated in the schema description).
_K0S_KNOWN_CONFIG_FILES: dict[str, str] = {
    "k0s.yaml": _K0S_CONFIG_PATH,
}


class K0sError(SSHError):
    """Raised when a k0s operation fails."""


class K0sInstaller(ClusterBackend):
    """
    Installs and configures k0s on remote cluster nodes.

    The deployment order must be::

    1. Install k0s binary on every host.
    2. Bootstrap the first controller (or sole-like) node — this produces both
       controller and worker join-tokens.
    3. Join additional controllers with the controller token.
    4. Join all worker nodes using the worker token.

    Parameters
    ----------
    config:
        Optional :class:`~zcc.models.backend.BackendConfig` from the cluster
        definition.  When provided, any ``config_files`` entries recognised by
        this backend (currently only ``k0s.yaml``) are uploaded to their
        well-known remote paths during :meth:`install`, and ``arguments`` are
        appended to every ``k0s install controller/worker`` invocation.
    """

    def __init__(self, config: "BackendConfig | None" = None) -> None:
        from ..models.backend import BackendConfig as _BackendConfig

        self._cfg = config if config is not None else _BackendConfig()

    # ------------------------------------------------------------------
    # Binary installation
    # ------------------------------------------------------------------

    def install(self, ssh: SSHClient) -> None:
        """Download and install the k0s binary on the remote host.

        After installing the binary, any config files declared in
        :attr:`BackendConfig.config_files` that are recognised by this backend
        are uploaded to their well-known remote paths so that subsequent
        ``k0s install controller/worker`` invocations can pick them up.
        """
        logger.info("[%s] Installing k0s binary", ssh.host.uri)
        ssh.run_checked(_K0S_INSTALL_SCRIPT)
        self._upload_config_files(ssh)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _upload_config_files(self, ssh: SSHClient) -> None:
        """Upload recognised config files from :class:`BackendConfig` to the remote.

        Each entry in :attr:`BackendConfig.config_files` whose key appears in
        :data:`_K0S_KNOWN_CONFIG_FILES` is written to the corresponding remote
        path.  The value may be either raw file content (inline YAML / TOML
        block scalar) or a local filesystem path; if the string resolves to an
        existing local file its contents are read and forwarded.

        Because well-known paths like ``/etc/k0s/k0s.yaml`` require root
        privileges, the content is first written to a temporary path via SFTP
        and then moved with ``sudo``.
        """
        for name, remote_path in _K0S_KNOWN_CONFIG_FILES.items():
            value = self._cfg.config_files.get(name)
            if value is None:
                continue

            # Resolve local file reference vs inline content.
            if os.path.isfile(value):
                with open(value, encoding="utf-8") as fh:
                    content = fh.read()
            else:
                content = value

            tmp_path = f"/tmp/zcc-{name}.tmp"
            remote_dir = remote_path.rsplit("/", 1)[0]
            logger.info("[%s] Uploading %s to %s", ssh.host.uri, name, remote_path)
            ssh.write_text(tmp_path, content)
            ssh.run_checked(f"sudo mkdir -p {remote_dir}")
            ssh.run_checked(f"sudo mv {tmp_path} {remote_path}")

    def _extra_args(self) -> str:
        """Return a space-prefixed CLI argument string from :attr:`BackendConfig.arguments`.

        Each ``{flag: value}`` pair in ``arguments`` is rendered as ``flag value``
        (or just ``flag`` when *value* is an empty string).  The result is ready
        to be appended to a ``k0s install controller/worker`` command.
        """
        parts: list[str] = []
        for flag, value in self._cfg.arguments.items():
            parts.append(f"{flag} {shlex.quote(str(value))}" if value else flag)
        return (" " + " ".join(parts)) if parts else ""

    def _config_flag(self, ssh: SSHClient) -> str:
        """Return a ``--config`` flag if a k0s YAML config exists on the remote.

        When :data:`_K0S_CONFIG_PATH` is present the file is passed to every
        ``k0s install`` call so that cluster-level settings (e.g. kube-router
        overlay mode) are applied.  The flag is omitted when the file does not
        exist so that :class:`K0sInstaller` continues to work on plain nodes
        that have no pre-configured k0s YAML.
        """
        code, _, _ = ssh.run(f"test -f {_K0S_CONFIG_PATH}")
        return f" --config {_K0S_CONFIG_PATH}" if code == 0 else ""

    # ------------------------------------------------------------------
    # Controller bootstrap
    # ------------------------------------------------------------------

    def init_controller(
        self, ssh: SSHClient, *, enable_workers: bool = False
    ) -> tuple[str, str]:
        """
        Install the k0s controller service and start it.

        Parameters
        ----------
        ssh:
            Open SSH connection to the controller node.
        enable_workers:
            When *True*, remove the control-plane NoSchedule taint to allow
            regular workloads on this controller (sole-like behavior).

        Returns
        -------
        tuple[str, str]
            ``(controller_token, worker_token)`` join tokens.
        """
        logger.info("[%s] Bootstrapping k0s controller", ssh.host.uri)

        config = self._config_flag(ssh)
        extra = self._extra_args()
        ssh.run_checked(f"sudo k0s install controller{config}{extra}")
        ssh.run_checked(_K0S_START)

        if enable_workers:
            self.enable_worker_scheduling(ssh)

        self._wait_for_ready(ssh)
        logger.info("[%s] Generating controller and worker join-tokens", ssh.host.uri)
        controller_token = ssh.run_checked("sudo k0s token create --role=controller")
        worker_token = ssh.run_checked("sudo k0s token create --role=worker")
        return controller_token.strip(), worker_token.strip()

    def enable_worker_scheduling(self, ssh: SSHClient) -> None:
        """Allow regular workloads on a controller by removing its NoSchedule taint."""
        logger.info("[%s] Removing controller NoSchedule taint", ssh.host.uri)
        ssh.run_checked(
            "sudo k0s kubectl taint nodes --all "
            "node-role.kubernetes.io/control-plane:NoSchedule- || true"
        )

    def join_controller(self, ssh: SSHClient, token: str) -> None:
        """
        Install the k0s controller service and join it to an existing cluster.

        Parameters
        ----------
        ssh:
            Open SSH connection to the controller node.
        token:
            Controller join-token produced by :meth:`init_controller`.
        """
        logger.info("[%s] Joining k0s cluster as controller", ssh.host.uri)
        ssh.write_text(_CONTROLLER_TOKEN_PATH, token, mode=0o600)
        config = self._config_flag(ssh)
        extra = self._extra_args()
        ssh.run_checked(
            f"sudo k0s install controller --token-file {_CONTROLLER_TOKEN_PATH}{config}{extra}"
        )
        ssh.run_checked(_K0S_START)

    # ------------------------------------------------------------------
    # Worker join
    # ------------------------------------------------------------------

    def join_worker(self, ssh: SSHClient, token: str) -> None:
        """
        Install the k0s worker service and join it to the cluster.

        Parameters
        ----------
        ssh:
            Open SSH connection to the worker node.
        token:
            Join-token produced by :meth:`init_controller`.
        """
        logger.info("[%s] Joining k0s cluster as worker", ssh.host.uri)
        ssh.write_text(_WORKER_TOKEN_PATH, token, mode=0o600)
        extra = self._extra_args()
        ssh.run_checked(f"sudo k0s install worker --token-file {_WORKER_TOKEN_PATH}{extra}")
        ssh.run_checked(_K0S_START)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self, ssh: SSHClient) -> str:
        """Return the k0s status output from a remote host."""
        code, out, err = ssh.run(_K0S_STATUS)
        if code != 0:
            return f"k0s not running or not installed ({err.strip()})"
        return out.strip()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _wait_for_ready(
        self, ssh: SSHClient, *, timeout: int = _K0S_READY_TIMEOUT
    ) -> None:
        """Poll the k0s API until the controller is ready to serve requests.

        After ``k0s start`` the controller needs up to ~60 s to initialise
        etcd and bring the API server online.  Attempting ``k0s token create``
        before it is ready returns an error, so we wait here.

        Parameters
        ----------
        ssh:
            Open SSH connection to the controller node.
        timeout:
            Maximum number of seconds to wait before raising
            :exc:`K0sError`.
        """
        logger.info(
            "[%s] Waiting for k0s API to become ready (up to %ds)",
            ssh.host.uri,
            timeout,
        )
        deadline = time.monotonic() + timeout
        while True:
            code, _, _ = ssh.run("sudo k0s kubectl get nodes")
            if code == 0:
                logger.info("[%s] k0s API is ready", ssh.host.uri)
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(5)
        raise K0sError(
            f"k0s API on {ssh.host.uri} did not become ready within {timeout}s"
        )
