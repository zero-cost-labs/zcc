"""Host model — represents a single node in the cluster."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .backend import BackendConfig
from .state import HostState

# ---------------------------------------------------------------------------
# Reserved labels understood by the framework.
# A host must carry at least one of these so zcc knows how to install k0s.
# ---------------------------------------------------------------------------
RESERVED_LABELS = frozenset({"controller", "worker", "sole"})


class PortDirection(str, Enum):
    IN = "in"
    OUT = "out"
    BOTH = "both"


class PortProtocol(str, Enum):
    TCP = "tcp"
    UDP = "udp"


class PortConfig(BaseModel):
    role: str
    number: int = Field(ge=1, le=65535)
    direction: PortDirection = PortDirection.BOTH
    type: PortProtocol = PortProtocol.TCP


class StoragePermission(str, Enum):
    R = "r"
    RM = "rm"
    W = "w"
    WM = "wm"
    RW = "rw"
    RWM = "rwm"


class StorageConfig(BaseModel):
    role: str
    location: str
    permissions: StoragePermission = StoragePermission.RW


class ResourceRole(str, Enum):
    CPU = "cpu"
    GPU = "gpu"
    RAM = "ram"
    VRAM = "vram"


class LimitConfig(BaseModel):
    role: ResourceRole
    percent: float = Field(ge=0.0, le=1.0)


class SSHConfig(BaseModel):
    """SSH connection parameters for a host."""

    user: str = "root"
    port: int = Field(default=22, ge=1, le=65535)
    key: Optional[str] = None
    password: Optional[str] = None
    known_hosts_file: Optional[str] = None  # None → use ~/.ssh/known_hosts


class Host(BaseModel):
    """A physical or virtual node that participates in the cluster."""

    name: str
    uri: str  # unique identifier — IP or resolvable hostname
    labels: list[str] = Field(min_length=1)
    # When True, prevents auto-sole promotion even when topology would normally
    # trigger it (all-controller cluster).  The node still runs as a pure k0s
    # controller without the --single flag.
    no_sole: bool = Field(default=False, alias="no-sole")
    ssh: SSHConfig = Field(default_factory=SSHConfig)
    ports: list[PortConfig] = []
    storage: list[StorageConfig] = []
    limits: list[LimitConfig] = []
    backend: BackendConfig = Field(default_factory=BackendConfig)

    # Runtime deployment tracking — excluded from serialization.
    deployment_state: HostState = Field(default=HostState.PENDING, exclude=True)

    model_config = {"populate_by_name": True}

    def has_label(self, *labels: str) -> bool:
        """Return True when the host carries at least one of *labels*."""
        return bool(set(self.labels) & set(labels))
