"""k0s installer — installs and bootstraps k0s on cluster nodes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .backend import ClusterBackend
from .ssh import SSHClient, SSHError

if TYPE_CHECKING:
    from ..models.host import Host

logger = logging.getLogger(__name__)

# k0s upstream installer script
_K0S_INSTALL_SCRIPT = "curl -sSLf https://get.k0s.sh | sudo sh"

# k0s service commands
_K0S_START = "sudo k0s start"
_K0S_STATUS = "sudo k0s status"
_CONTROLLER_TOKEN_PATH = "/tmp/k0s-controller-token"
_WORKER_TOKEN_PATH = "/tmp/k0s-worker-token"


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
    """

    # ------------------------------------------------------------------
    # Binary installation
    # ------------------------------------------------------------------

    def install(self, ssh: SSHClient) -> None:
        """Download and install the k0s binary on the remote host."""
        logger.info("[%s] Installing k0s binary", ssh.host.uri)
        ssh.run_checked(_K0S_INSTALL_SCRIPT)

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

        ssh.run_checked("sudo k0s install controller")
        ssh.run_checked(_K0S_START)

        if enable_workers:
            self.enable_worker_scheduling(ssh)

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
        ssh.run_checked(
            f"sudo k0s install controller --token-file {_CONTROLLER_TOKEN_PATH}"
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
        ssh.run_checked(f"sudo k0s install worker --token-file {_WORKER_TOKEN_PATH}")
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
