"""Feature model — a capability deployed to selected cluster nodes."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from .state import FeatureState


class Feature(BaseModel):
    """
    A feature (program, configuration, or storage capability) deployed to
    every cluster node that carries at least one of the feature's labels.
    Reserved labels (``controller``, ``worker``, ``sole``) may be used here
    to target nodes by their k0s role.

    When ``singleton`` is ``True`` the feature is deployed to exactly **one**
    of the matching nodes (the first in cluster-definition order) instead of
    all matching nodes.  This is useful for features that must run on a
    single node — for example a shared database or a unique message broker —
    where deploying to every labelled host would create duplicates.

    Features may be specified in three ways inside a cluster definition:

    * **Inline** — all fields provided directly in the cluster YAML.
    * **By URI** — ``uri`` points to a standalone feature YAML file;
      the file is loaded by the :func:`~zcc.loader.load_cluster` loader and
      merged with any inline overrides before validation.
    * **By name** — only ``name`` is given (no ``labels``, no ``uri``);
      the loader resolves it to ``features/<name>.yaml`` relative to the
      cluster file.

    For URI and name-based references ``labels`` (and optionally ``name``)
    may be omitted — they are supplied by the external file.  Inline fields
    always take precedence over values from the referenced file.
    """

    name: str | None = None
    uri: str | None = None
    labels: list[str] = []
    singleton: bool = False
    locations: list[str] = []
    install_cmds: list[str] = Field(default=[], alias="install-cmds")
    init_cmds: list[str] = Field(default=[], alias="init-cmds")

    # Runtime deployment tracking — excluded from serialization.
    deployment_state: FeatureState = Field(default=FeatureState.PENDING, exclude=True)
    deployed_to: list[str] = Field(default_factory=list, exclude=True)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _validate_completeness(self) -> "Feature":
        """Inline features must be fully specified; references need name or uri."""
        if not self.name and not self.uri:
            raise ValueError("Feature must specify 'name' or 'uri'")
        # When no uri is present the feature must be fully defined inline:
        # both name and labels are required.  (Name-only and URI references
        # are resolved by the loader before this validator runs.)
        if not self.uri and not self.labels:
            raise ValueError(
                f"Inline feature '{self.name}' must have at least one label"
            )
        return self
