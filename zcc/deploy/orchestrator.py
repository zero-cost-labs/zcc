"""Deployment orchestrator — wires together k0s and feature deployment."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..models.host import Host
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

    1. Install k0s binary on **all** hosts.
    2. Bootstrap the first controller/sole node and obtain the worker join-token.
    3. Bootstrap remaining controller nodes.
    4. Join all worker nodes using the join-token.
    5. Deploy features to their target hosts (matched by label).
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

        # Install k0s on every node first.
        for host in self.cluster.hosts:
            with SSHClient(host) as ssh:
                self._k0s.install(ssh)

        # Bootstrap controllers / sole nodes.
        worker_token: str | None = None
        primary = controllers[0]
        is_single = primary.has_label("sole") or (
            not workers and len(controllers) == 1
        )

        with SSHClient(primary) as ssh:
            worker_token = self._k0s.init_controller(ssh, single=is_single)

        for ctrl in controllers[1:]:
            with SSHClient(ctrl) as ssh:
                self._k0s.init_controller(ssh)

        # Join workers.
        if worker_token:
            for worker in workers:
                with SSHClient(worker) as ssh:
                    self._k0s.join_worker(ssh, worker_token)

        # Deploy features.
        for feature in self.cluster.features:
            for host in self._resolve_targets(feature):
                with SSHClient(host) as ssh:
                    self._features.deploy(ssh, feature)

        logger.info("Cluster '%s' deployed successfully", self.cluster.name)

    def plan(self) -> list[str]:
        """
        Return a human-readable list of actions that :meth:`deploy` would
        perform, without executing anything.
        """
        lines: list[str] = [f"Cluster: {self.cluster.name}"]

        lines.append("\nNodes:")
        for host in self.cluster.hosts:
            lines.append(
                f"  {host.name} ({host.uri})  labels: {', '.join(host.labels)}"
            )

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
        """Return (controllers_and_soles, pure_workers).

        A host is a controller candidate when it carries the ``controller``
        or ``sole`` label.  A pure worker carries ``worker`` but not ``sole``.
        """
        controllers = [
            h for h in self.cluster.hosts if h.has_label("controller", "sole")
        ]
        workers = [
            h
            for h in self.cluster.hosts
            if "worker" in h.labels and "sole" not in h.labels
        ]
        return controllers, workers

    def _resolve_targets(self, feature: "Feature") -> list[Host]:
        """Return hosts that carry at least one of the feature's labels."""
        feature_labels = set(feature.labels)
        return [
            h for h in self.cluster.hosts if set(h.labels) & feature_labels
        ]
