"""Feature model — a capability deployed to selected cluster nodes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Feature(BaseModel):
    """
    A feature (program, configuration, or storage capability) deployed to
    every cluster node that carries at least one of the feature's labels.
    Reserved labels (``controller``, ``worker``, ``sole``) may be used here
    to target nodes by their k0s role.
    """

    name: str
    labels: list[str] = Field(min_length=1)
    locations: list[str] = []
    install_cmds: list[str] = Field(default=[], alias="install-cmds")
    init_cmds: list[str] = Field(default=[], alias="init-cmds")

    model_config = {"populate_by_name": True}
