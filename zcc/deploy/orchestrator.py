"""Deployment orchestrator — wires together a cluster backend and feature deployment."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Callable

from ..models.backend import BackendType
from ..models.host import Host
from .backend import ClusterBackend
from .feature import FeatureDeployer
from .k0s import K0sInstaller
from .ssh import SSHClient
from .swarm import DockerSwarmBackend

if TYPE_CHECKING:
    from ..models.cluster import Cluster
    from ..models.feature import Feature
    from .translation import BackendTranslator

logger = logging.getLogger(__name__)


class DeployOrchestrator:
    """Orchestrates the full deployment of a :class:`~zcc.models.cluster.Cluster`.

    **Sole-node semantics**

    A controller runs as *sole* (control-plane + workloads on one node)
    when any of the following is true:

    * The host explicitly carries the ``sole`` label, **or**
    * The cluster contains no non-controller hosts (auto-sole topology)
      and the host has not set ``no-sole: true``.

    A host with ``no-sole: true`` always runs as a pure controller
    regardless of topology.

    **Label absorption**

    Sole nodes act as a catch-all target for features whose labels do not
    match any explicitly labelled host.  Once a non-controller node is
    added that carries those labels the explicit match takes precedence and
    the sole node no longer receives the feature.

    **Deployment order**

    1. Install the backend on **all** hosts (parallel).
    2. Bootstrap the primary controller/sole node → obtain controller +
       worker tokens.
    3. Join remaining controller nodes (parallel).
    4. Join all non-controller nodes as workers (parallel).
    5. Deploy features to their target hosts (per-feature, parallel).

    **Heterogeneous backends**

    When the cluster contains hosts with different
    :class:`~zcc.models.backend.BackendType` values, a
    :class:`~zcc.deploy.translation.BackendTranslator` must be registered
    for every distinct ``(source, target)`` pair via the ``translators``
    constructor parameter.  Without a matching translator the deployment
    will raise :exc:`RuntimeError` when a cross-backend join is attempted.

    Parameters
    ----------
    cluster :
        Fully validated :class:`~zcc.models.cluster.Cluster` to deploy.
    backend :
        Optional single :class:`ClusterBackend` override.  When supplied
        it is used for **all** hosts regardless of their declared backend
        type.  Primarily useful for testing.
    translators :
        Optional list of :class:`~zcc.deploy.translation.BackendTranslator`
        instances.  Each translator bridges one ``(source, target)`` backend
        type pair.  Required when the cluster defines heterogeneous backends.
    """

    def __init__(
        self,
        cluster: "Cluster",
        backend: ClusterBackend | None = None,
        translators: "list[BackendTranslator] | None" = None,
    ) -> None:
        self.cluster = cluster
        self._backends: dict[BackendType, ClusterBackend] = (
            self._build_backend_registry(backend)
        )
        self._translators: dict[tuple[BackendType, BackendType], "BackendTranslator"] = (
            {(t.source_type, t.target_type): t for t in (translators or [])}
        )
        self._features = FeatureDeployer()

    # ------------------------------------------------------------------
    # Backward-compatible single-backend accessor
    # ------------------------------------------------------------------

    @property
    def _backend(self) -> ClusterBackend:
        """Return the backend for the primary (first-host) backend type.

        This property exists for backward compatibility with code and tests
        that refer to ``orchestrator._backend``.  In heterogeneous clusters
        use :meth:`_backend_for` to obtain the correct backend per host.
        """
        return self._backends[self.cluster.hosts[0].backend.type]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def deploy(self) -> None:
        """Run the full cluster deployment."""
        logger.info("Starting deployment of cluster '%s'", self.cluster.name)

        controllers, non_controllers = self._split_hosts()

        # Step 1 — install the backend on every node in parallel.
        self._run_parallel(self._install_on, self.cluster.hosts)

        # Step 2 — bootstrap the primary controller.
        primary = controllers[0]
        primary_backend = self._backend_for(primary)
        with SSHClient(primary) as ssh:
            controller_token, worker_token = primary_backend.init_controller(
                ssh, enable_workers=self._is_sole(primary)
            )

        # Step 3 — join remaining controllers in parallel.
        def _init_ctrl(host: Host) -> None:
            join_token = self._join_token_for(
                controller_token, primary.backend.type, host
            )
            with SSHClient(host) as ssh:
                self._backend_for(host).join_controller(ssh, join_token)
                if self._is_sole(host):
                    self._backend_for(host).enable_worker_scheduling(ssh)

        if controller_token.strip() and controllers[1:]:
            self._run_parallel(_init_ctrl, controllers[1:])

        # Step 4 — join non-controller nodes as workers in parallel.
        if worker_token.strip() and non_controllers:
            def _join(host: Host) -> None:
                join_token = self._join_token_for(
                    worker_token, primary.backend.type, host
                )
                with SSHClient(host) as ssh:
                    self._backend_for(host).join_worker(ssh, join_token)

            self._run_parallel(_join, non_controllers)

        # Step 5 — deploy features; each feature fans out to targets in parallel.
        for feature in self.cluster.features:
            targets = self._resolve_targets(feature)

            def _deploy(host: Host, feat: "Feature" = feature) -> None:
                with SSHClient(host) as ssh:
                    self._features.deploy(ssh, feat)

            self._run_parallel(_deploy, targets)

        logger.info("Cluster '%s' deployed successfully", self.cluster.name)

    def plan(self) -> list[str]:
        """Return a human-readable list of actions that :meth:`deploy` would
        perform, without executing anything.
        """
        lines: list[str] = [f"Cluster: {self.cluster.name}"]

        lines.append("\nNodes:")
        for host in self.cluster.hosts:
            role = self._effective_role(host)
            lines.append(
                f"  [{role}] {host.name} ({host.uri})"
                f"  labels: {', '.join(host.labels)}"
            )

        lines.append("\nFeatures:")
        for feature in self.cluster.features:
            targets = self._resolve_targets(feature)
            target_names = ", ".join(h.name for h in targets) or "(none)"
            # Indicate when a feature lands on sole nodes via label absorption.
            feature_labels = set(feature.labels)
            has_explicit = any(
                set(h.labels) & feature_labels for h in self.cluster.hosts
            )
            suffix = (
                "  \u2190 absorbed by sole"
                if (not has_explicit and targets)
                else ""
            )
            singleton_note = "  [singleton]" if feature.singleton else ""
            lines.append(f"  {feature.name} \u2192 {target_names}{suffix}{singleton_note}")

        return lines

    # ------------------------------------------------------------------
    # Backend registry helpers
    # ------------------------------------------------------------------

    def _build_backend_registry(
        self, override: ClusterBackend | None
    ) -> dict[BackendType, ClusterBackend]:
        """Build a ``{BackendType: ClusterBackend}`` registry for the cluster.

        When *override* is supplied it is stored under **every** backend
        type present in the cluster so that :meth:`_backend_for` always
        returns it — this preserves the existing single-backend injection
        contract used by tests and external callers.

        When no override is given a fresh backend instance is created for
        each distinct type declared across all hosts.  The first host that
        declares a given type wins (its :class:`~zcc.models.backend.BackendConfig`
        is passed to the backend constructor).
        """
        if override is not None:
            return {h.backend.type: override for h in self.cluster.hosts}

        registry: dict[BackendType, ClusterBackend] = {}
        for host in self.cluster.hosts:
            bt = host.backend.type
            if bt in registry:
                continue
            if bt == BackendType.K0S:
                registry[bt] = K0sInstaller(host.backend)
            elif bt == BackendType.SWARM:
                registry[bt] = DockerSwarmBackend(host.backend)
            else:
                raise RuntimeError(
                    f"Unknown backend type '{bt.value}' on host '{host.name}'. "
                    "Add a backend constructor branch in "
                    "DeployOrchestrator._build_backend_registry()."
                )
        return registry

    def _backend_for(self, host: Host) -> ClusterBackend:
        """Return the :class:`ClusterBackend` appropriate for *host*.

        Raises
        ------
        RuntimeError
            If no backend has been registered for the host's type.  This
            should never happen on a valid, fully validated cluster.
        """
        bt = host.backend.type
        try:
            return self._backends[bt]
        except KeyError:
            raise RuntimeError(
                f"No backend registered for type '{bt.value}' "
                f"(host '{host.name}'). "
                "This is a bug — the cluster validator should have caught "
                "this before deployment."
            ) from None

    def _translator_for(
        self, source: BackendType, target: BackendType
    ) -> "BackendTranslator | None":
        """Return the registered translator for the ``(source, target)`` pair.

        Returns ``None`` when no translator has been registered.
        """
        return self._translators.get((source, target))

    def _join_token_for(
        self, token: str, primary_type: BackendType, target_host: Host
    ) -> str:
        """Return the join token appropriate for *target_host*.

        When *target_host* runs the same backend type as *primary_type*
        the token is returned unchanged.  When the types differ a
        :class:`~zcc.deploy.translation.BackendTranslator` is looked up
        and invoked to produce a translated token.

        Raises
        ------
        RuntimeError
            When the types differ and no translator has been registered for
            the ``(primary_type, target_type)`` pair.
        """
        target_type = target_host.backend.type
        if target_type == primary_type:
            return token

        translator = self._translator_for(primary_type, target_type)
        if translator is None:
            raise RuntimeError(
                f"Cannot join host '{target_host.name}' "
                f"(backend: {target_type.value}) to a cluster whose "
                f"primary controller uses backend {primary_type.value}: "
                f"no BackendTranslator registered for "
                f"{primary_type.value}→{target_type.value}. "
                "Register a BackendTranslator before calling deploy()."
            )

        source_backend = self._backends[primary_type]
        target_backend = self._backends[target_type]
        return translator.translate_join_token(token, source_backend, target_backend)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split_hosts(self) -> tuple[list[Host], list[Host]]:
        """Return ``(controllers, non_controllers)``.

        A host is a *controller* when it carries the ``controller`` or
        ``sole`` label.  Every other host is a *non-controller* and will
        be joined as a worker regardless of which user-defined labels it
        carries.
        """
        controllers = [
            h for h in self.cluster.hosts if h.has_label("controller", "sole")
        ]
        non_controllers = [
            h
            for h in self.cluster.hosts
            if not h.has_label("controller", "sole")
        ]
        return controllers, non_controllers

    def _is_sole(self, host: Host) -> bool:
        """Return True when *host* should run as sole (controller + workloads).

        Decision order:

        1. If ``no-sole: true`` → **never** sole (pure controller).
        2. If the host carries the ``sole`` label explicitly → always sole.
        3. Auto-sole: when the cluster has **no** non-controller hosts and
           the host has not opted out.
        """
        if host.no_sole:
            return False
        if "sole" in host.labels:
            return True
        _, non_controllers = self._split_hosts()
        return len(non_controllers) == 0

    def _sole_hosts(self) -> list[Host]:
        """Return the subset of controllers that run (or will run) as sole."""
        controllers, _ = self._split_hosts()
        return [h for h in controllers if self._is_sole(h)]

    def _effective_role(self, host: Host) -> str:
        """Human-readable effective role for *host* (used in plan output)."""
        controllers, _ = self._split_hosts()
        if host in controllers:
            return "sole" if self._is_sole(host) else "controller"
        return "worker"

    def _resolve_targets(self, feature: "Feature") -> list[Host]:
        """Return hosts that should receive *feature*.

        Matching is label-based: a host is a target when it carries at
        least one of the feature's labels.

        **Label absorption**: when no host explicitly carries a matching
        label, sole nodes act as the catch-all fallback and receive the
        feature.

        **Singleton**: when ``feature.singleton`` is ``True`` only the
        first matching host (in cluster-definition order) is returned.
        """
        feature_labels = set(feature.labels)
        explicit_matches = [
            h for h in self.cluster.hosts if set(h.labels) & feature_labels
        ]
        if explicit_matches:
            targets = explicit_matches
        else:
            # No explicit match → fall back to sole nodes.
            targets = self._sole_hosts()
        if feature.singleton:
            return targets[:1]
        return targets

    def _install_on(self, host: Host) -> None:
        with SSHClient(host) as ssh:
            self._backend_for(host).install(ssh)

    def _run_parallel(
        self, fn: Callable[[Host], None], items: list[Host]
    ) -> None:
        """Execute *fn(item)* for each item in *items* concurrently.

        All items are submitted at once; any exceptions are collected and
        re-raised after all futures complete so that a single failure does
        not silently suppress errors from other hosts.
        """
        if not items:
            return
        errors: list[Exception] = []
        with ThreadPoolExecutor() as executor:
            futures = {executor.submit(fn, item): item for item in items}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001 — intentional collection
                    errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise RuntimeError(
                f"{len(errors)} deployment errors occurred:\n"
                + "\n".join(f"  \u2022 {e}" for e in errors)
            )
