"""Cluster model — the top-level configuration object."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .feature import Feature
from .host import Host, HostRole


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

    @field_validator("hosts")
    @classmethod
    def validate_hosts(cls, hosts: list[Host]) -> list[Host]:
        # At least one node must act as a control-plane member.
        control_roles = {HostRole.CONTROLLER, HostRole.SOLE}
        has_controller = any(
            bool(set(h.roles) & control_roles) for h in hosts
        )
        if not has_controller:
            raise ValueError(
                "Cluster must have at least one host with role 'controller' or 'sole'"
            )

        # Host URIs must be unique (they are the identity field).
        uris = [h.uri for h in hosts]
        if len(uris) != len(set(uris)):
            raise ValueError("Host URIs must be unique across the cluster")

        return hosts

    @field_validator("features")
    @classmethod
    def validate_features(cls, features: list[Feature]) -> list[Feature]:
        names = [f.name for f in features]
        if len(names) != len(set(names)):
            raise ValueError("Feature names must be unique within the cluster")
        return features
