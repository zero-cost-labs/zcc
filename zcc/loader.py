"""YAML cluster config loader with validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .models.cluster import Cluster


class ConfigError(Exception):
    """Raised when a cluster configuration file is invalid."""


def _merge_labels(file_labels: list, inline_labels: list) -> list:
    """Merge inline labels onto a base label list from a feature file.

    Inline labels are **appended** to the file's labels.  A label that
    starts with ``"-"`` removes the corresponding label (without the
    prefix) from the inherited set rather than adding a new one.

    Parameters
    ----------
    file_labels:
        Labels declared in the external feature file.
    inline_labels:
        Labels declared in the cluster-level entry for the feature.

    Returns
    -------
    list
        Merged label list: ``file_labels`` minus any negations, plus
        any additions, preserving order.
    """
    negated = {lbl[1:] for lbl in inline_labels if lbl.startswith("-")}
    additions = [lbl for lbl in inline_labels if not lbl.startswith("-")]
    return [lbl for lbl in file_labels if lbl not in negated] + additions


def _resolve_feature_refs(
    features: list[Any], base_dir: Path
) -> list[dict]:
    """Resolve name-based and URI-based feature references to full dicts.

    Three entry forms are accepted:

    * **Inline** — has ``name`` *and* ``labels``; returned as-is.
    * **By URI** — has ``uri``; the file at ``<base_dir>/<uri>`` is loaded
      and merged with any other inline fields (inline values win).
    * **By name** — has ``name`` but no ``labels`` and no ``uri``; the file
      ``<base_dir>/features/<name>.yaml`` is loaded and merged with the
      inline entry.

    For entries resolved from an external file, **labels are merged**
    rather than replaced: inline labels are appended to the file's labels.
    A label that starts with ``"-"`` removes the corresponding label from
    the inherited set (e.g. ``"-worker"`` removes ``"worker"``).  All
    other inline fields still fully override their file counterparts.

    Parameters
    ----------
    features:
        The raw ``features`` list from the cluster YAML (list of dicts).
    base_dir:
        Directory containing the cluster YAML file; used to resolve
        relative URIs and the conventional ``features/`` directory.

    Returns
    -------
    list[dict]
        Fully-populated feature dictionaries ready for Pydantic validation.
    """
    resolved: list[dict] = []
    for entry in features:
        if not isinstance(entry, dict):
            resolved.append(entry)
            continue

        uri: str | None = entry.get("uri")
        name: str | None = entry.get("name")
        labels: list | None = entry.get("labels")

        # Determine the external feature file path (if any).
        feature_path: Path | None = None
        if uri:
            # Explicit URI reference.
            feature_path = (base_dir / uri).resolve()
        elif name and not labels:
            # Name-only reference: look in features/<name>.yaml by convention.
            feature_path = (base_dir / "features" / f"{name}.yaml").resolve()

        if feature_path is None:
            # Fully-inline entry — no resolution needed.
            resolved.append(entry)
            continue

        # Load the external feature file.
        try:
            file_text = feature_path.read_text(encoding="utf-8")
        except OSError as exc:
            ref_desc = uri or f"features/{name}.yaml"
            raise ConfigError(
                f"Cannot read feature file '{ref_desc}': {exc}"
            ) from exc

        try:
            file_data = yaml.safe_load(file_text)
        except yaml.YAMLError as exc:
            ref_desc = uri or f"features/{name}.yaml"
            raise ConfigError(
                f"Invalid YAML in feature file '{ref_desc}': {exc}"
            ) from exc

        if not isinstance(file_data, dict):
            ref_desc = uri or f"features/{name}.yaml"
            raise ConfigError(
                f"Feature file '{ref_desc}' must be a YAML mapping"
            )

        # Merge: file provides defaults; inline fields take precedence.
        # Exception: labels are appended (not replaced); a label starting
        # with "-" removes the matching label from the file's list.
        merged = {**file_data, **entry}
        inline_labels: list | None = entry.get("labels")
        if inline_labels is not None:
            merged["labels"] = _merge_labels(
                list(file_data.get("labels", [])), inline_labels
            )
        resolved.append(merged)

    return resolved


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
        Any feature references (by name or URI) are resolved before the
        cluster model is validated.

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

    # Resolve feature references (URI and name-based) before cluster validation.
    if isinstance(data.get("features"), list):
        data["features"] = _resolve_feature_refs(data["features"], path.parent)

    try:
        return Cluster.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(
            f"Cluster config '{path}' failed validation:\n{exc}"
        ) from exc
