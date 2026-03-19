"""YAML cluster config loader with validation."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models.cluster import Cluster


class ConfigError(Exception):
    """Raised when a cluster configuration file is invalid."""


def load_cluster(path: str | Path) -> Cluster:
    """
    Parse and validate a cluster YAML configuration file.

    Parameters
    ----------
    path:
        Filesystem path to the cluster YAML file.

    Returns
    -------
    Cluster
        A fully validated :class:`~zcc.models.cluster.Cluster` instance.

    Raises
    ------
    ConfigError
        When the file cannot be read, parsed, or validated.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read config file '{path}': {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in '{path}': {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"Config file '{path}' must be a YAML mapping")

    try:
        return Cluster.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(
            f"Cluster config '{path}' failed validation:\n{exc}"
        ) from exc
