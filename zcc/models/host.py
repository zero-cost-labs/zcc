"""Host model — represents a single node in the cluster."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class HostRole(str, Enum):
    """Role a node plays within the cluster."""

    CONTROLLER = "controller"
    WORKER = "worker"
    SOLE = "sole"  # controller + worker on a single node


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
    roles: list[HostRole] = Field(min_length=1)
    labels: list[str] = []
    ssh: SSHConfig = Field(default_factory=SSHConfig)
    ports: list[PortConfig] = []
    storage: list[StorageConfig] = []
    limits: list[LimitConfig] = []

