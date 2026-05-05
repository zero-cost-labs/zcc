"""Behavioral state machine for cluster deployment.

This module owns everything related to deployment *behavior* — the
exception raised when a state transition is invalid and any helpers that
the orchestrator uses to enforce or inspect state.

The *type definitions* for the individual state values (the enums) live
in :mod:`zcc.models.state` because they are used as Pydantic field types
on the model classes.  What belongs here is the runtime contract: what
it means to violate a state constraint.
"""

from __future__ import annotations


class DeploymentPendingError(RuntimeError):
    """Raised when deployment is attempted on a cluster that is not READY.

    A cluster is in DRAFT state when no host carries a ``controller`` or
    ``sole`` label.  Add a primary control node to transition the cluster
    to READY and retry deployment.
    """
