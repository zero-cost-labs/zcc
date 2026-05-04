"""Cluster lifecycle state."""

from __future__ import annotations

from enum import Enum


class ClusterState(str, Enum):
    """Lifecycle state of a :class:`~zcc.models.cluster.Cluster`.

    DRAFT
        No primary control node has been assigned yet.  The configuration
        is accepted and stored, but deployment is deferred until a
        ``controller`` or ``sole`` host is added.
    READY
        A primary control node is present.  The cluster's authoritative
        backend type is known and deployment can proceed.
    """

    DRAFT = "draft"
    READY = "ready"
