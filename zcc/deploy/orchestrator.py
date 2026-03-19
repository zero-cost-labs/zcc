"""Deployment orchestrator — wires together k0s and feature deployment."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..models.host import Host, HostRole
from .feature import FeatureDeployer
from .k0s import K0sInstaller
from .ssh import SSHClient

if TYPE_CHECKING:
    from ..models.cluster import Cluster
    from ..models.feature import Feature

logger = logging.getLogger(__name__)


class DeployOrchestrator:
    """
    Orchestrates the full deployment of a :class:`~zcc.models.cluster.Cluster`.

    Deployment order:

    1. Install k0s binary on **all** hosts in parallel (sequentially for now).
    2. Bootstrap the first controller/sole node and obtain the worker join-token.
    3. Bootstrap remaining controller nodes.
    4. Join all worker nodes using the join-token.
    5. Deploy features to their target hosts.
    """

    def __init__(self, cluster: "Cluster") -> None:
        self.cluster = cluster
        self._k0s = K0sInstaller()
        self._features = FeatureDeployer()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def deploy(self) -> None:
        """Run the full cluster deployment."""
        logger.info("Starting deployment of cluster '%s'", self.cluster.name)

        controllers, workers = self._split_hosts()
        sole_nodes = [h for h in controllers if HostRole.SOLE in h.roles]
        pure_controllers = [h for h in controllers if HostRole.SOLE not in h.roles]

        # Install k0s on every node first.
        for host in self.cluster.hosts:
            with SSHClient(host) as ssh:
                self._k0s.install(ssh)

        # Bootstrap controllers.
        worker_token: str | None = None
        primary = (pure_controllers or sole_nodes)[0]
        is_single = (HostRole.SOLE in primary.roles) or (not workers and len(controllers) == 1)

        with SSHClient(primary) as ssh:
            worker_token = self._k0s.init_controller(ssh, single=is_single)

        for ctrl in (pure_controllers + sole_nodes)[1:]:
            with SSHClient(ctrl) as ssh:
                self._k0s.init_controller(ssh)

        # Join workers.
        if worker_token:
            for worker in workers:
                with SSHClient(worker) as ssh:
                    self._k0s.join_worker(ssh, worker_token)

        # Deploy features.
        for feature in self.cluster.features:
            targets = self._resolve_targets(feature)
            for host in targets:
                with SSHClient(host) as ssh:
                    self._features.deploy(ssh, feature)

        logger.info("Cluster '%s' deployed successfully", self.cluster.name)

    def plan(self) -> list[str]:
        """
        Return a human-readable list of actions that :meth:`deploy` would
        perform, without executing anything.
        """
        lines: list[str] = [f"Cluster: {self.cluster.name}"]
        controllers, workers = self._split_hosts()

        lines.append("\nNodes:")
        for host in self.cluster.hosts:
            roles_str = ", ".join(r.value for r in host.roles)
            lines.append(f"  [{roles_str}] {host.name} ({host.uri})")

        lines.append("\nFeatures:")
        for feature in self.cluster.features:
            targets = self._resolve_targets(feature)
            target_names = ", ".join(h.name for h in targets) or "(none)"
            lines.append(f"  {feature.name} → {target_names}")

        return lines

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split_hosts(self) -> tuple[list[Host], list[Host]]:
        """Return (controllers_and_soles, pure_workers)."""
        control_roles = {HostRole.CONTROLLER, HostRole.SOLE}
        controllers = [h for h in self.cluster.hosts if set(h.roles) & control_roles]
        workers = [
            h
            for h in self.cluster.hosts
            if HostRole.WORKER in h.roles and HostRole.SOLE not in h.roles
        ]
        return controllers, workers

    def _resolve_targets(self, feature: "Feature") -> list[Host]:
        """Return hosts that match the feature's label or role selectors.

        Label matching takes precedence; role matching is used as a fallback so
        that a host matching *both* criteria is never added twice.
        """
        targets: list[Host] = []
        feature_roles = set(feature.roles)
        feature_labels = set(feature.labels)

        for host in self.cluster.hosts:
            host_roles = {r.value for r in host.roles}
            host_labels = set(host.labels)

            if feature_labels & host_labels:
                targets.append(host)
            elif feature_roles & host_roles:
                targets.append(host)

        return targets
