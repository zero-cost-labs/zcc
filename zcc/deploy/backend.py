"""Abstract cluster backend interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ssh import SSHClient


class ClusterBackend(ABC):
    """
    Abstract interface for cluster backends.

    A backend encapsulates how to install and wire up cluster nodes.
    Concrete implementations (e.g. :class:`~zcc.deploy.k0s.K0sInstaller`)
    provide the backend-specific commands while
    :class:`~zcc.deploy.orchestrator.DeployOrchestrator` drives the overall
    topology and sequencing.
    """

    @abstractmethod
    def install(self, ssh: "SSHClient") -> None:
        """Install the backend binary / agent on the remote host."""

    @abstractmethod
    def init_controller(
        self, ssh: "SSHClient", *, enable_workers: bool = False
    ) -> tuple[str, str]:
        """
        Bootstrap the primary controller node.

        Parameters
        ----------
        ssh:
            Open SSH connection to the primary controller.
        enable_workers:
            When *True*, configure the controller to also schedule regular
            workloads (sole-node semantics).

        Returns
        -------
        tuple[str, str]
            ``(controller_token, worker_token)`` used to join additional nodes.
        """

    @abstractmethod
    def enable_worker_scheduling(self, ssh: "SSHClient") -> None:
        """Allow regular workloads on a controller (sole-node semantics)."""

    @abstractmethod
    def join_controller(self, ssh: "SSHClient", token: str) -> None:
        """Join an additional controller node to the cluster using *token*."""

    @abstractmethod
    def join_worker(self, ssh: "SSHClient", token: str) -> None:
        """Join a worker node to the cluster using *token*."""

    @abstractmethod
    def status(self, ssh: "SSHClient") -> str:
        """Return a human-readable status string for the node."""
