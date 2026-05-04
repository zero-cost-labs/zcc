"""Cluster model — the top-level configuration object."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from .backend import BackendConfig, BackendType
from .feature import Feature
from .host import Host
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
    every ``(source, target)`` backend-type pair among the cluster's hosts
    — otherwise validation fails.

    This means:

    * ``cluster.backend`` and ``cluster.primary_controller`` always reflect
      the main control node, not necessarily ``hosts[0]``.
    * Per-host ``backend.arguments`` and ``backend`` config files are fully
      independent; different hosts of the *same* type can have different
      arguments.

    See :class:`~zcc.models.translation.TranslationRecipe` and
    :class:`~zcc.deploy.translation.BackendTranslator` for the extension
    points that federation work hooks into.

    .. note:: Future — state-machine / deferred deployment

        A future state-machine design may allow cluster definitions to be
        stored before the main control node has been identified or deployed.
        In that deferred case the configuration is accepted as a *draft* and
        deployment waits until a control node is committed.  For now we
        require the main control node to be present in the first manifest
        (enforced by :meth:`validate_hosts`).  All places where that
        assumption is baked in are marked with ``# TODO(state-machine)``
        comments.
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
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def primary_controller(self) -> Host:
        """The main control node — the first host carrying ``controller`` or ``sole``.

        This host's backend type is the authoritative cluster-level backend.
        All other hosts may run different backends; when they do a
        :class:`~zcc.models.translation.TranslationRecipe` is required for
        each distinct ``(source, target)`` pair.

        On a fully-validated :class:`Cluster` this property always succeeds
        because :meth:`validate_hosts` already requires at least one
        controller/sole host.

        .. note:: TODO(state-machine)

            A future design may allow clusters to be defined before a
            controller host is assigned (draft / pending state).  In that
            scenario this property would raise, and callers that drive
            deployment must check for the absence of a primary controller
            and wait until one is committed.  The validator below
            (:meth:`validate_hosts`) is the natural place to relax the
            "controller required" rule when that state-machine is introduced.
        """
        for host in self.hosts:
            if host.has_label("controller", "sole"):
                return host
        # TODO(state-machine): when deferred-deployment mode is added this
        # branch will become the normal "draft cluster" path instead of an
        # error.  For now a validated Cluster always has a controller, so
        # this line should never be reached on a well-formed instance.
        raise AttributeError(
            "No main control node found.  The cluster has no host labelled "
            "'controller' or 'sole'.  This should have been caught by "
            "validate_hosts — this is a bug."
        )

    @property
    def backend(self) -> BackendConfig:
        """Cluster-level backend configuration, determined by the main control node.

        Returns the :class:`~zcc.models.backend.BackendConfig` of the
        :attr:`primary_controller` — the first host in the manifest that
        carries a ``controller`` or ``sole`` label.  That host's backend
        type is the authoritative, cluster-wide default.

        Worker nodes or secondary controllers may carry a *different*
        backend type.  When they do, callers that need per-host precision
        should iterate over :attr:`hosts` directly and inspect each host's
        ``backend`` field rather than relying on this property.

        .. note:: TODO(state-machine)

            In a future deferred-deployment design this property would raise
            (or return ``None``) when no primary controller is present yet.
            Deployment code must check for that condition and wait.  See
            :attr:`primary_controller` for the full discussion.
        """
        return self.primary_controller.backend

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("hosts")
    @classmethod
    def validate_hosts(cls, hosts: list[Host]) -> list[Host]:
        """Validate structural constraints on the host list.

        Rules enforced here:

        1. At least one host must carry a ``controller`` or ``sole`` label
           — that host is the **main control node** and determines the
           cluster-level backend.
        2. Host URIs must be unique (they serve as the node identity).

        .. note:: TODO(state-machine)

            Rule 1 may be relaxed in a future state-machine design where a
            cluster definition can be committed before a controller is
            assigned.  In that draft state the cluster is valid but
            deployment is deferred until a controller host is added.
            Introducing that change requires replacing this hard rejection
            with a "draft" status flag and adding a gate in the deployment
            pipeline.

        Backend-type compatibility across hosts is validated separately in
        :meth:`validate_translation_coverage` because it requires access to
        the ``translations`` field, which is not available inside a
        ``field_validator``.
        """
        # TODO(state-machine): the "controller required" check below is the
        # first place to soften when deferred-deployment is introduced.
        # At least one node must carry a control-plane label.
        has_controller = any(
            h.has_label("controller", "sole") for h in hosts
        )
        if not has_controller:
            raise ValueError(
                "Cluster must have at least one host labelled 'controller' or 'sole'"
            )

        # Host URIs must be unique (they are the identity field).
        uris = [h.uri for h in hosts]
        if len(uris) != len(set(uris)):
            raise ValueError("Host URIs must be unique across the cluster")

        return hosts

    @model_validator(mode="after")
    def validate_translation_coverage(self) -> "Cluster":
        """Ensure translation recipes cover all cross-backend type pairs.

        The cluster's authoritative backend type is that of the
        :attr:`primary_controller`.  Any host whose ``backend.type``
        differs from the primary controller's type introduces a
        heterogeneous pairing that requires a bridging recipe.

        Specifically, a :class:`~zcc.models.translation.TranslationRecipe`
        must exist for **every distinct** ``(source, target)`` ordered pair
        among all backend types present in the cluster — in both directions,
        because join-token exchange is asymmetric.

        This validator runs after all fields are parsed (``mode="after"``)
        so that the ``translations`` list is available for cross-field
        checks.

        Raises
        ------
        ValueError
            When heterogeneous backend types are found and one or more
            ``(source, target)`` pairs lack a translation recipe.

        .. note:: TODO(state-machine)

            When the deferred-deployment design is introduced, clusters in
            "draft" state (no primary controller yet) will bypass this check
            and re-run it once the controller is committed.
        """
        types_present: set[BackendType] = {h.backend.type for h in self.hosts}

        if len(types_present) <= 1:
            # Homogeneous cluster — nothing more to check.
            return self

        # The primary controller's type is the cluster's authoritative backend.
        # Every other type present introduces a (primary→other) and
        # (other→primary) pair that needs a recipe.
        primary_type = self.primary_controller.backend.type

        # Build the set of all ordered (source, target) pairs that need a recipe.
        # We require coverage in both directions because token exchange is
        # asymmetric: the primary controller issues tokens, and other-backend
        # nodes must both receive *and* potentially re-issue them.
        needed: set[tuple[BackendType, BackendType]] = {
            (a, b) for a in types_present for b in types_present if a != b
        }

        # Recipes already declared in the cluster definition.
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
        """Validate feature uniqueness constraints.

        Named features (inline definitions and resolved URI refs) must have
        unique names.  Unresolved URI-only entries must also have unique
        URIs so the same feature file is not included twice.
        """
        # Named features must have unique names.
        names = [f.name for f in features if f.name is not None]
        if len(names) != len(set(names)):
            raise ValueError("Feature names must be unique within the cluster")

        # Unresolved URI-only entries must have unique URIs.
        uris = [f.uri for f in features if f.name is None and f.uri is not None]
        if len(uris) != len(set(uris)):
            raise ValueError("Feature URIs must be unique within the cluster")

        return features
