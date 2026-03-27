"""Backend configuration model."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class BackendConfig(BaseModel):
    """
    Backend-specific deployment configuration.

    ``arguments`` holds arbitrary key→value pairs forwarded to the backend
    as CLI flags or configuration options.  Every other key in this section
    is treated as a *configuration file* entry: the key is the filename and
    the value is either the raw file content (a YAML block scalar) or an
    absolute / relative filesystem path to an existing file.

    Example::

        backend:
          arguments:
            --network: calico
          "k0s.yaml": |
            apiVersion: k0s.k0sproject.io/v1beta1
            ...
          "containerd.toml": "/etc/containerd/config.toml"
    """

    model_config = ConfigDict(extra="allow")

    arguments: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _validate_config_files(cls, data: Any) -> Any:
        """Ensure all non-reserved keys map to string values."""
        if isinstance(data, dict):
            reserved = {"arguments"}
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
