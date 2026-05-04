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

    Backend homogeneity
    -------------------
    All hosts must run the **same** backend type (e.g. all k0s, or all
    Docker Swarm).  If hosts with *different* backend types are declared,
    a :class:`~zcc.models.translation.TranslationRecipe` must be provided
    for every distinct ``(source, target)`` pair — otherwise the cluster
    fails validation.

    See :class:`~zcc.models.translation.TranslationRecipe` and
    :class:`~zcc.deploy.translation.BackendTranslator` for the extension
    points that future federation work can hook into.
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

    @property
    def backend(self) -> BackendConfig:
        """Backend configuration derived from the first host.

        Returns the :class:`~zcc.models.backend.BackendConfig` of the
        first host as the authoritative cluster-level reference.  This is
        a convenience accessor for the common *homogeneous* case where all
        hosts share the same backend type.

        When the cluster contains **heterogeneous** backends (multiple
        :class:`~zcc.models.backend.BackendType` values across hosts),
        callers should iterate over ``hosts`` directly and inspect each
        host's ``backend`` field rather than relying on this property.

        ``hosts`` always contains at least one element (enforced by
        ``min_length=1``), so ``hosts[0]`` is always safe to access on a
        fully validated model instance.
        """
        return self.hosts[0].backend

    @field_validator("hosts")
    @classmethod
    def validate_hosts(cls, hosts: list[Host]) -> list[Host]:
        """Validate structural constraints on the host list.

        Rules:

        1. At least one host must carry a ``controller`` or ``sole`` label.
        2. Host URIs must be unique (they serve as the node identity).

        Backend-type homogeneity is validated separately in
        :meth:`validate_backend_homogeneity` because it requires access to
        the ``translations`` field, which is not available inside a
        ``field_validator``.
        """
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
    def validate_backend_homogeneity(self) -> "Cluster":
        """Enforce backend-type homogeneity across all hosts.

        A cluster is valid when **one** of the following is true:

        * All hosts declare the **same** backend type (homogeneous).
        * Hosts declare **different** backend types *and* the cluster's
          ``translations`` list covers **every** distinct
          ``(source, target)`` pair among those types.

        This validator runs after all fields are fully parsed so that the
        ``translations`` list is available for cross-field checks.

        Raises
        ------
        ValueError
            When heterogeneous backend types are found and one or more
            ``(source, target)`` pairs lack a translation recipe.
        """
        types_present: set[BackendType] = {h.backend.type for h in self.hosts}

        if len(types_present) <= 1:
            # Homogeneous cluster — nothing more to check.
            return self

        # Build the set of all ordered (source, target) pairs that need a
        # recipe (every combination in both directions).
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
                f"Heterogeneous backend types detected ({types_str}) but no "
                f"translation recipe provided for: {missing_str}. "
                "Add a TranslationRecipe for each missing pair under the "
                "'translations' cluster key, or ensure all hosts use the "
                "same backend type. "
                "Note: heterogeneous backends cannot form a single native "
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
