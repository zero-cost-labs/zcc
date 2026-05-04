"""Backend configuration model."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class BackendType(str, Enum):
    """Identifies which cluster-backend technology a host uses.

    A cluster is **homogeneous** by default: all hosts must declare the
    same :class:`BackendType`.  When hosts with *different* types are
    needed a :class:`~zcc.models.translation.TranslationRecipe` must be
    provided for every distinct ``(source, target)`` pair — otherwise the
    cluster fails validation.

    .. note:: Intrinsic limitations

        k0s and Docker Swarm are architecturally incompatible:

        * **Control-plane gap** — k0s uses etcd + the Kubernetes API server;
          Swarm uses Raft consensus embedded in the Docker daemon.  Neither
          speaks the other's wire protocol.
        * **Join-protocol gap** — k0s tokens are opaque to Docker Swarm and
          vice versa; a bridge must issue separate credentials on each side.
        * **Overlay-network gap** — k0s relies on CNI plugins; Swarm
          maintains its own VXLAN mesh.  Cross-backend pod/service
          communication requires an external fabric (e.g. Submariner, Cilium
          Cluster Mesh, or a custom service mesh).

        A concrete :class:`~zcc.deploy.translation.BackendTranslator`
        therefore needs to deploy a federation layer *above* both platforms.

    Values
    ------
    K0S
        `k0s <https://k0sproject.io>`_ — a lightweight Kubernetes
        distribution.  This is the **default**.
    SWARM
        `Docker Swarm <https://docs.docker.com/engine/swarm/>`_ —
        Docker's built-in container-orchestration mode.
    """

    K0S = "k0s"
    SWARM = "swarm"


class BackendConfig(BaseModel):
    """Backend-specific deployment configuration for a single host.

    ``type`` selects which cluster-backend technology the host runs
    (default: :attr:`BackendType.K0S`).  All hosts in a cluster must
    declare the same ``type`` unless a
    :class:`~zcc.models.translation.TranslationRecipe` covering all
    ``(source, target)`` pairs is supplied at the cluster level.

    Because ``BackendConfig`` is host-scoped, different hosts within the
    *same* backend type may carry different ``arguments`` and config files
    (e.g. a GPU node with a custom ``containerd.toml``).

    ``arguments`` holds arbitrary key→value pairs forwarded to the backend
    as CLI flags or configuration options.  Every other key is treated as a
    *configuration file* entry: the key is the destination filename and the
    value is either raw file content (a YAML block scalar) or a local
    filesystem path to an existing file.

    Example::

        backend:
          type: k0s          # explicit; same as the default
          arguments:
            --network: calico
          "k0s.yaml": |
            apiVersion: k0s.k0sproject.io/v1beta1
            ...
          "containerd.toml": "/etc/containerd/config.toml"
    """

    model_config = ConfigDict(extra="allow")

    type: BackendType = BackendType.K0S
    arguments: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _validate_config_files(cls, data: Any) -> Any:
        """Ensure all non-reserved keys map to string values."""
        if isinstance(data, dict):
            reserved = {"type", "arguments"}
            for key, value in data.items():
                if key not in reserved and not isinstance(value, str):
                    raise ValueError(
                        f"Config file entry '{key}' must be a string "
                        "(inline content or a filesystem path)"
                    )
        return data

    @property
    def config_files(self) -> dict[str, str]:
        """Return all extra keys as ``{filename: content_or_path}`` mappings."""
        return dict(self.model_extra or {})
