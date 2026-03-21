"""Docker Swarm backend — installs and bootstraps Docker Swarm on cluster nodes."""

from __future__ import annotations

import logging

from .backend import ClusterBackend
from .ssh import SSHClient, SSHError

logger = logging.getLogger(__name__)

# Docker upstream installer script
_DOCKER_INSTALL_SCRIPT = "curl -fsSL https://get.docker.com | sudo sh"

# Docker Swarm commands
_SWARM_STATUS = "sudo docker info --format '{{.Swarm.LocalNodeState}}'"


class DockerSwarmError(SSHError):
    """Raised when a Docker Swarm operation fails."""


class DockerSwarmBackend(ClusterBackend):
    """
    Installs and configures Docker Swarm on remote cluster nodes.

    The deployment order must be::

    1. Install Docker Engine on every host.
    2. Bootstrap the first manager (or standalone) node — this produces both
       manager and worker join-tokens.
    3. Join additional managers with the manager token.
    4. Join all worker nodes using the worker token.

    The manager's advertise address is recorded during :meth:`init_controller`
    and reused automatically by :meth:`join_controller` and
    :meth:`join_worker`.  Both join methods must therefore be called **after**
    :meth:`init_controller` has run at least once.
    """

    def __init__(self) -> None:
        self._manager_addr: str = ""

    # ------------------------------------------------------------------
    # Docker Engine installation
    # ------------------------------------------------------------------

    def install(self, ssh: SSHClient) -> None:
        """Download and install Docker Engine on the remote host."""
        logger.info("[%s] Installing Docker Engine", ssh.host.uri)
        ssh.run_checked(_DOCKER_INSTALL_SCRIPT)

    # ------------------------------------------------------------------
    # Swarm bootstrap
    # ------------------------------------------------------------------

    def init_controller(
        self, ssh: SSHClient, *, enable_workers: bool = False
    ) -> tuple[str, str]:
        """
        Initialise a new Docker Swarm and start the primary manager.

        Parameters
        ----------
        ssh:
            Open SSH connection to the primary manager node.
        enable_workers:
            Ignored — Docker Swarm managers always accept workloads by default.
            Present to satisfy the :class:`~zcc.deploy.backend.ClusterBackend`
            interface.

        Returns
        -------
        tuple[str, str]
            ``(manager_token, worker_token)`` join tokens.
        """
        logger.info("[%s] Initialising Docker Swarm", ssh.host.uri)
        self._manager_addr = f"{ssh.host.uri}:2377"
        ssh.run_checked(
            f"sudo docker swarm init --advertise-addr {ssh.host.uri}"
        )
        logger.info("[%s] Generating manager and worker join-tokens", ssh.host.uri)
        manager_token = ssh.run_checked("sudo docker swarm join-token manager -q")
        worker_token = ssh.run_checked("sudo docker swarm join-token worker -q")
        return manager_token.strip(), worker_token.strip()

    def enable_worker_scheduling(self, ssh: SSHClient) -> None:
        """No-op — Docker Swarm managers schedule workloads by default."""
        logger.debug(
            "[%s] Docker Swarm managers run workloads by default; nothing to do",
            ssh.host.uri,
        )

    def join_controller(self, ssh: SSHClient, token: str) -> None:
        """
        Join an additional manager node to the Docker Swarm.

        Parameters
        ----------
        ssh:
            Open SSH connection to the manager node.
        token:
            Manager join-token produced by :meth:`init_controller`.
        """
        if not self._manager_addr:
            raise DockerSwarmError(
                "Manager address is not set; call init_controller first"
            )
        logger.info("[%s] Joining Docker Swarm as manager", ssh.host.uri)
        ssh.run_checked(
            f"sudo docker swarm join --token {token} {self._manager_addr}"
        )

    # ------------------------------------------------------------------
    # Worker join
    # ------------------------------------------------------------------

    def join_worker(self, ssh: SSHClient, token: str) -> None:
        """
        Join a worker node to the Docker Swarm.

        Parameters
        ----------
        ssh:
            Open SSH connection to the worker node.
        token:
            Worker join-token produced by :meth:`init_controller`.
        """
        if not self._manager_addr:
            raise DockerSwarmError(
                "Manager address is not set; call init_controller first"
            )
        logger.info("[%s] Joining Docker Swarm as worker", ssh.host.uri)
        ssh.run_checked(
            f"sudo docker swarm join --token {token} {self._manager_addr}"
        )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self, ssh: SSHClient) -> str:
        """Return the Docker Swarm node state from a remote host."""
        code, out, err = ssh.run(_SWARM_STATUS)
        if code != 0:
            return f"docker not running or not installed ({err.strip()})"
        return out.strip()
