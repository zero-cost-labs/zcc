"""State enum type definitions for cluster resources.

All enums here are pure type definitions consumed as model field types.
Transition logic (who drives the transitions and when) lives in
:mod:`zcc.deploy.state` and :mod:`zcc.deploy.orchestrator`.
"""

from __future__ import annotations

from enum import Enum


class ClusterState(str, Enum):
    """Lifecycle state of a :class:`~zcc.models.cluster.Cluster`.

    DRAFT
        No primary control node has been assigned yet.  The configuration
        is accepted and stored, but deployment is deferred until a
        ``controller`` or ``sole`` host is added.
    READY
        A primary control node is present and the cluster is ready to be
        deployed, but deployment has not started yet.
    DEPLOYING
        Deployment is currently in progress: at least one host is being
        installed or joined.
    DEPLOYED
        All hosts have been successfully installed/joined **and** all
        features have been deployed to their target hosts.
    """

    DRAFT = "draft"
    READY = "ready"
    DEPLOYING = "deploying"
    DEPLOYED = "deployed"


class HostState(str, Enum):
    """Deployment state of a single :class:`~zcc.models.host.Host`.

    PENDING
        The host has not been touched by the deployer yet.
    DEPLOYING
        The backend is being installed or the host is joining the cluster.
    DEPLOYED
        The host has successfully completed all deployment steps.
    """

    PENDING = "pending"
    DEPLOYING = "deploying"
    DEPLOYED = "deployed"


class FeatureState(str, Enum):
    """Deployment state of a :class:`~zcc.models.feature.Feature`.

    PENDING
        The feature has not been deployed to any of its target hosts yet.
    PARTIAL
        The feature has been deployed to *some* target hosts but not all.
    DEPLOYED
        The feature has been deployed to every target host (or there are
        no target hosts, in which case deployment is trivially complete).
    """

    PENDING = "pending"
    PARTIAL = "partial"
    DEPLOYED = "deployed"
