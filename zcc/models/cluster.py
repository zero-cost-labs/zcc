"""Cluster model — the top-level configuration object."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from .backend import BackendConfig, BackendType
from .feature import Feature
from .host import Host
from .state import ClusterState
from .translation import TranslationRecipe


class Cluster(BaseModel):
    """A zero-cost cluster definition.

    Describes the nodes (hosts), the capabilities (features) to deploy,
    and supplementary config paths.

    Cluster-level backend
    ----------------------
    The cluster's authoritative backend type is determined **solely** by
    the **main control node** — the first host in the manifest that carries
    a ``controller`` or ``sole`` label.  Worker nodes and secondary
    controllers may run a *different* backend type; that is explicitly
    supported.  When heterogeneous backends coexist, a
    :class:`~zcc.models.translation.TranslationRecipe` must exist for
    every ``(source, target)`` backend-type pair — otherwise validation
    fails.

    Cluster lifecycle
    -----------------
    A cluster may be created **before** a control node is known.  In that
    case :attr:`state` is :attr:`~zcc.models.state.ClusterState.DRAFT`;
    :attr:`primary_controller` and :attr:`backend` both return ``None``,
    and deployment is deferred until a ``controller`` / ``sole`` host is
    added (at which point :attr:`state` becomes
    :attr:`~zcc.models.state.ClusterState.READY`).

    See :class:`~zcc.models.translation.TranslationRecipe` and
    :class:`~zcc.deploy.translation.BackendTranslator` for the extension
    points that federation work hooks into.
    """

    name: str
    version: str = "v1"
    hosts: list[Host] = Field(min_length=1)
    features: list[Feature] = []
    config: list[str] = []
    translations: list[TranslationRecipe] = Field(
        default_factory=list,
        description=(
            "Translation recipes that allow heterogeneous backends to "
            "coexist in a single cluster definition.  Each recipe covers "
            "one (source, target) backend-type pair.  When hosts with "
            "different backend types are declared, every distinct "
            "(source, target) pair must have a matching recipe; otherwise "
            "the cluster is invalid."
        ),
    )

    # ------------------------------------------------------------------
    # Lifecycle state
    # ------------------------------------------------------------------

    @property
    def state(self) -> ClusterState:
        """Current lifecycle state of this cluster.

        :attr:`~zcc.models.state.ClusterState.DRAFT` when no host carries
        a ``controller`` or ``sole`` label; deployment is deferred.
        :attr:`~zcc.models.state.ClusterState.READY` once a primary
        control node is present.
        """
        return (
            ClusterState.READY
            if any(h.has_label("controller", "sole") for h in self.hosts)
            else ClusterState.DRAFT
        )

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def primary_controller(self) -> Host | None:
        """The main control node, or ``None`` when the cluster is in DRAFT state.

        Returns the first host carrying a ``controller`` or ``sole`` label.
        That host's backend type is the authoritative cluster-level backend.

        Returns ``None`` when :attr:`state` is
        :attr:`~zcc.models.state.ClusterState.DRAFT` — no controller has
        been assigned yet.  Callers that drive deployment must check for
        ``None`` and defer until a controller is committed.
        """
        for host in self.hosts:
            if host.has_label("controller", "sole"):
                return host
        return None  # DRAFT state — no controller yet

    @property
    def backend(self) -> BackendConfig | None:
        """Cluster-level backend, or ``None`` when in DRAFT state.

        Returns the :class:`~zcc.models.backend.BackendConfig` of the
        :attr:`primary_controller` when the cluster is READY; ``None``
        otherwise.  Callers must handle the ``None`` case and defer
        deployment until the cluster transitions to READY.
        """
        ctrl = self.primary_controller
        return ctrl.backend if ctrl is not None else None

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("hosts")
    @classmethod
    def validate_hosts(cls, hosts: list[Host]) -> list[Host]:
        """Validate structural constraints on the host list.

        Rules enforced here:

        1. Host URIs must be unique (they serve as the node identity).

        A controller/sole host is *not* required at validation time — a
        cluster without one is accepted in DRAFT state and deployment is
        simply deferred (see :attr:`state`).  The deployment pipeline
        enforces the READY gate via
        :exc:`~zcc.deploy.orchestrator.DeploymentPendingError`.

        Backend-type compatibility is validated separately in
        :meth:`validate_translation_coverage`.
        """
        uris = [h.uri for h in hosts]
        if len(uris) != len(set(uris)):
            raise ValueError("Host URIs must be unique across the cluster")
        return hosts

    @model_validator(mode="after")
    def validate_translation_coverage(self) -> "Cluster":
        """Ensure translation recipes cover all cross-backend type pairs.

        Skipped automatically when the cluster is in DRAFT state (no
        primary controller present yet) because the authoritative backend
        type is not yet known.

        When READY, a :class:`~zcc.models.translation.TranslationRecipe`
        must exist for **every distinct** ``(source, target)`` ordered pair
        among all backend types in the cluster.

        Raises
        ------
        ValueError
            When heterogeneous backend types are found and one or more
            pairs lack a translation recipe.
        """
        if self.state is ClusterState.DRAFT:
            return self  # authoritative backend unknown — check deferred

        types_present: set[BackendType] = {h.backend.type for h in self.hosts}
        if len(types_present) <= 1:
            return self

        primary_type = self.primary_controller.backend.type  # type: ignore[union-attr]
        needed: set[tuple[BackendType, BackendType]] = {
            (a, b) for a in types_present for b in types_present if a != b
        }
        covered: set[tuple[BackendType, BackendType]] = {
            (r.source, r.target) for r in self.translations
        }
        missing = needed - covered
        if missing:
            missing_str = ", ".join(
                f"{s.value}→{t.value}"
                for s, t in sorted(missing, key=lambda p: (p[0].value, p[1].value))
            )
            types_str = ", ".join(sorted(v.value for v in types_present))
            raise ValueError(
                f"The main control node uses backend '{primary_type.value}', but "
                f"the cluster also contains hosts with types ({types_str}).  "
                f"No translation recipe provided for: {missing_str}.  "
                "Add a TranslationRecipe for each missing pair under the "
                "'translations' cluster key.  "
                "Note: different backend types cannot natively join the same "
                "cluster — a concrete BackendTranslator must be implemented "
                "to bridge the incompatible control planes, join protocols, "
                "and overlay networks."
            )
        return self

    @field_validator("features")
    @classmethod
    def validate_features(cls, features: list[Feature]) -> list[Feature]:
        """Validate feature uniqueness constraints."""
        names = [f.name for f in features if f.name is not None]
        if len(names) != len(set(names)):
            raise ValueError("Feature names must be unique within the cluster")
        uris = [f.uri for f in features if f.name is None and f.uri is not None]
        if len(uris) != len(set(uris)):
            raise ValueError("Feature URIs must be unique within the cluster")
        return features
