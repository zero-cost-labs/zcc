"""k0s installer — installs and bootstraps k0s on cluster nodes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .ssh import SSHClient, SSHError

if TYPE_CHECKING:
    from ..models.host import Host

logger = logging.getLogger(__name__)

# k0s upstream installer script
_K0S_INSTALL_SCRIPT = "curl -sSLf https://get.k0s.sh | sudo sh"

# k0s service commands
_K0S_START = "sudo k0s start"
_K0S_STATUS = "sudo k0s status"


class K0sError(SSHError):
    """Raised when a k0s operation fails."""


class K0sInstaller:
    """
    Installs and configures k0s on remote cluster nodes.

    The deployment order must be::

        1. Install k0s binary on every host.
        2. Bootstrap the first controller (or sole) node — this produces a
           worker join-token.
        3. Join all worker nodes using that token.
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

    def init_controller(self, ssh: SSHClient, *, single: bool = False) -> str:
        """
        Install the k0s controller service and start it.

        Parameters
        ----------
        ssh:
            Open SSH connection to the controller node.
        single:
            When *True*, passes ``--single`` to k0s, making the node run both
            the control plane and workloads (equivalent to the ``sole`` role).

        Returns
        -------
        str
            A worker join-token that can be passed to :meth:`join_worker`.
        """
        logger.info("[%s] Bootstrapping k0s controller", ssh.host.uri)

        install_cmd = "sudo k0s install controller"
        if single:
            install_cmd += " --single"
        ssh.run_checked(install_cmd)
        ssh.run_checked(_K0S_START)

        logger.info("[%s] Generating worker join-token", ssh.host.uri)
        token = ssh.run_checked("sudo k0s token create --role=worker")
        return token.strip()

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
        # Write the token via stdin to avoid exposing it in process listings.
        if ssh._client is None:
            raise K0sError("Not connected")
        _, stdin, _ = ssh._client.exec_command(
            "sudo tee /tmp/k0s-token > /dev/null"
        )
        stdin.write(token)
        stdin.channel.shutdown_write()
        ssh.run_checked("sudo k0s install worker --token-file /tmp/k0s-token")
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
