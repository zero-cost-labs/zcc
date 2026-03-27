"""Cluster model — the top-level configuration object."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .backend import BackendConfig
from .feature import Feature
from .host import Host


class Cluster(BaseModel):
    """
    A zero-cost cluster definition.  Describes the nodes (hosts), the
    capabilities (features) to deploy, and supplementary config paths.
    """

    name: str
    version: str = "v1"
    hosts: list[Host] = Field(min_length=1)
    features: list[Feature] = []
    config: list[str] = []

    @property
    def backend(self) -> BackendConfig:
        """Backend configuration for the cluster, derived from the hosts.

        Since heterogeneous backends are not supported all hosts must carry the
        same backend configuration.  This property returns the backend from the
        first host as the authoritative cluster-level reference.

        ``hosts`` always contains at least one element (enforced by the
        ``min_length=1`` field constraint), so ``hosts[0]`` is always safe to
        access on a fully validated model instance.
        """
        return self.hosts[0].backend

    @field_validator("hosts")
    @classmethod
    def validate_hosts(cls, hosts: list[Host]) -> list[Host]:
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

        # All hosts must use the same backend configuration.
        if len(hosts) > 1:
            first = hosts[0].backend
            for host in hosts[1:]:
                if host.backend != first:
                    raise ValueError(
                        "All hosts must use the same backend configuration"
                    )

        return hosts

    @field_validator("features")
    @classmethod
    def validate_features(cls, features: list[Feature]) -> list[Feature]:
        # Named features (inline definitions and already-resolved URI refs)
        # must have unique names.
        names = [f.name for f in features if f.name is not None]
        if len(names) != len(set(names)):
            raise ValueError("Feature names must be unique within the cluster")

        # Unresolved URI-only entries (name=None) must also have unique URIs so
        # the same feature file is not included twice.  After the loader resolves
        # all references to named features the first check above takes over.
        uris = [f.uri for f in features if f.name is None and f.uri is not None]
        if len(uris) != len(set(uris)):
            raise ValueError("Feature URIs must be unique within the cluster")

        return features
