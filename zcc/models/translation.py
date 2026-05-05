"""Backend translation model — describes how to bridge heterogeneous backends."""

from __future__ import annotations

from pydantic import BaseModel, model_validator

from .backend import BackendType


class TranslationRecipe(BaseModel):
    """Describes a bridging recipe between two heterogeneous cluster backends.

    When a cluster contains nodes with **different** backend types (e.g.
    k0s on some nodes and Docker Swarm on others), a
    :class:`TranslationRecipe` must exist for **every distinct**
    ``(source, target)`` type pair present in the cluster's host
    definitions.

    Without a recipe the cluster validator rejects mixed-backend
    definitions outright: k0s and Docker Swarm use incompatible control
    planes, join protocols, and overlay networks — nodes running different
    backends cannot natively form a single cluster.

    This model is the **data-model extension point** for future federation
    work.  The corresponding runtime logic lives in
    :class:`~zcc.deploy.translation.BackendTranslator`.  No concrete
    translation is implemented today; the presence of a recipe in the
    cluster definition *unlocks validation* so that the matching
    :class:`~zcc.deploy.translation.BackendTranslator` can be registered
    and invoked at deploy time.

    .. note:: Intrinsic limitations

        Bridging k0s (Kubernetes) and Docker Swarm natively requires a
        meta-scheduler or federation layer.  Any concrete
        :class:`TranslationRecipe` must account for at least:

        * **Control-plane gap** — each backend runs its own consensus
          protocol; a gateway or proxy must front both APIs.
        * **Join-protocol gap** — each backend's join token is opaque to
          the other; the translator must issue equivalent tokens or handle
          the join out-of-band.
        * **Overlay-network gap** — CNI (k0s) and Swarm VXLAN networks are
          independent; a cross-cluster network fabric (e.g. Submariner,
          Cilium Cluster Mesh) is required for pod-to-service connectivity.

    Parameters
    ----------
    source :
        The backend type that *initiates* the connection / sends the token.
    target :
        The backend type that *receives* the connection / consumes the
        token.

    Example YAML
    ------------
    ::

        translations:
          - source: k0s
            target: swarm
          - source: swarm
            target: k0s

    .. note::
        Both directions are usually required because join-token exchange is
        asymmetric.  Include both ``(source, target)`` and
        ``(target, source)`` recipes unless one direction is provably
        unnecessary for your topology.
    """

    source: BackendType
    target: BackendType

    @model_validator(mode="after")
    def _source_and_target_must_differ(self) -> "TranslationRecipe":
        """Reject a recipe that maps a backend type to itself."""
        if self.source == self.target:
            raise ValueError(
                "A TranslationRecipe must bridge two *different* backend "
                f"types; source and target are both '{self.source.value}'"
            )
        return self
