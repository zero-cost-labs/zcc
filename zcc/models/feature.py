"""Feature model — a capability deployed to selected cluster nodes."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class Feature(BaseModel):
    """
    A feature (program, configuration, or storage capability) deployed to
    cluster nodes that match by label or role.
    """

    name: str
    labels: list[str] = []
    roles: list[str] = []
    locations: list[str] = []
    install_cmds: list[str] = Field(default=[], alias="install-cmds")
    init_cmds: list[str] = Field(default=[], alias="init-cmds")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def has_target_selector(self) -> "Feature":
        if not self.labels and not self.roles:
            raise ValueError(
                f"Feature '{self.name}' must specify at least one label or role "
                "to target hosts"
            )
        return self
