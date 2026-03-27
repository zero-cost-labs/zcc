"""Tests for Pydantic models: Host, Feature, Cluster."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zcc.models.cluster import Cluster
from zcc.models.feature import Feature
from zcc.models.host import (
    Host,
    LimitConfig,
    PortConfig,
    ResourceRole,
    SSHConfig,
    StorageConfig,
    StoragePermission,
)


# ---------------------------------------------------------------------------
# Host
# ---------------------------------------------------------------------------


class TestHost:
    def _minimal(self, **overrides) -> dict:
        data = {
            "name": "node-1",
            "uri": "10.0.0.1",
            "labels": ["controller"],
        }
        data.update(overrides)
        return data

    def test_valid_controller_label(self):
        host = Host.model_validate(self._minimal())
        assert host.name == "node-1"
        assert "controller" in host.labels

    def test_valid_worker_with_extra_labels(self):
        host = Host.model_validate(
            self._minimal(labels=["worker", "compute", "gpu"])
        )
        assert "worker" in host.labels
        assert "compute" in host.labels

    def test_valid_sole_label(self):
        host = Host.model_validate(self._minimal(labels=["sole"]))
        assert "sole" in host.labels

    def test_multiple_labels_including_role(self):
        host = Host.model_validate(
            self._minimal(labels=["controller", "monitoring"])
        )
        assert "controller" in host.labels
        assert "monitoring" in host.labels

    def test_missing_labels_fails(self):
        with pytest.raises(ValidationError):
            Host.model_validate({"name": "n", "uri": "10.0.0.1"})

    def test_empty_labels_fails(self):
        with pytest.raises(ValidationError):
            Host.model_validate(self._minimal(labels=[]))

    def test_has_label_helper_true(self):
        host = Host.model_validate(self._minimal(labels=["worker", "compute"]))
        assert host.has_label("compute") is True
        assert host.has_label("worker", "gpu") is True

    def test_has_label_helper_false(self):
        host = Host.model_validate(self._minimal(labels=["worker"]))
        assert host.has_label("controller") is False

    def test_ssh_defaults(self):
        host = Host.model_validate(self._minimal())
        assert host.ssh.user == "root"
        assert host.ssh.port == 22
        assert host.ssh.key is None

    def test_ssh_custom(self):
        host = Host.model_validate(
            self._minimal(ssh={"user": "ubuntu", "port": 2222, "key": "/home/me/.ssh/id_rsa"})
        )
        assert host.ssh.user == "ubuntu"
        assert host.ssh.port == 2222

    def test_no_sole_default_false(self):
        host = Host.model_validate(self._minimal())
        assert host.no_sole is False

    def test_no_sole_can_be_set_true(self):
        host = Host.model_validate(self._minimal(**{"no-sole": True}))
        assert host.no_sole is True

    def test_no_sole_python_name_works(self):
        host = Host.model_validate(self._minimal(no_sole=True))
        assert host.no_sole is True

    def test_custom_only_labels_valid(self):
        """A host with only user-defined labels (no reserved) is valid at host level."""
        host = Host.model_validate(
            self._minimal(labels=["compute", "gpu", "high-memory"])
        )
        assert "compute" in host.labels
        assert host.no_sole is False

    def test_limit_percent_out_of_range(self):
        with pytest.raises(ValidationError):
            Host.model_validate(
                self._minimal(limits=[{"role": "cpu", "percent": 1.5}])
            )

    def test_limit_percent_valid_boundaries(self):
        host = Host.model_validate(
            self._minimal(
                limits=[
                    {"role": "cpu", "percent": 0.0},
                    {"role": "ram", "percent": 1.0},
                ]
            )
        )
        assert host.limits[0].percent == 0.0
        assert host.limits[1].percent == 1.0

    def test_storage_with_permissions(self):
        host = Host.model_validate(
            self._minimal(
                storage=[{"role": "data", "location": "/mnt/data", "permissions": "rw"}]
            )
        )
        assert host.storage[0].permissions == StoragePermission.RW

    def test_port_config(self):
        host = Host.model_validate(
            self._minimal(
                ports=[{"role": "api", "number": 8080, "direction": "in", "type": "tcp"}]
            )
        )
        assert host.ports[0].number == 8080


# ---------------------------------------------------------------------------
# Feature
# ---------------------------------------------------------------------------


class TestFeature:
    def test_valid_with_user_labels(self):
        feat = Feature.model_validate(
            {"name": "monitoring", "labels": ["compute"]}
        )
        assert feat.name == "monitoring"
        assert feat.labels == ["compute"]

    def test_valid_targeting_reserved_label(self):
        feat = Feature.model_validate(
            {"name": "core", "labels": ["controller"]}
        )
        assert feat.labels == ["controller"]

    def test_valid_mixed_labels(self):
        feat = Feature.model_validate(
            {"name": "full", "labels": ["primary", "worker"]}
        )
        assert "primary" in feat.labels
        assert "worker" in feat.labels

    def test_no_labels_fails(self):
        """A feature without labels must be rejected."""
        with pytest.raises(ValidationError):
            Feature.model_validate({"name": "orphan"})

    def test_empty_labels_fails(self):
        with pytest.raises(ValidationError):
            Feature.model_validate({"name": "orphan", "labels": []})

    def test_no_name_and_no_uri_fails(self):
        """A feature entry with neither name nor uri must be rejected."""
        with pytest.raises(ValidationError):
            Feature.model_validate({"labels": ["worker"]})

    def test_install_cmds_alias(self):
        feat = Feature.model_validate(
            {
                "name": "app",
                "labels": ["worker"],
                "install-cmds": ["helm install app ./chart"],
                "init-cmds": ["kubectl apply -f ./init.yaml"],
            }
        )
        assert feat.install_cmds == ["helm install app ./chart"]
        assert feat.init_cmds == ["kubectl apply -f ./init.yaml"]

    def test_locations(self):
        feat = Feature.model_validate(
            {"name": "app", "labels": ["x"], "locations": ["./charts/app"]}
        )
        assert feat.locations == ["./charts/app"]

    def test_singleton_default_false(self):
        feat = Feature.model_validate({"name": "app", "labels": ["worker"]})
        assert feat.singleton is False

    def test_singleton_true(self):
        feat = Feature.model_validate(
            {"name": "db", "labels": ["storage"], "singleton": True}
        )
        assert feat.singleton is True

    def test_singleton_false_explicit(self):
        feat = Feature.model_validate(
            {"name": "app", "labels": ["compute"], "singleton": False}
        )
        assert feat.singleton is False

    # ------------------------------------------------------------------
    # URI-reference form
    # ------------------------------------------------------------------

    def test_uri_only_is_valid(self):
        """A feature with only a uri is a valid unresolved reference."""
        feat = Feature.model_validate({"uri": "./features/monitoring.yaml"})
        assert feat.uri == "./features/monitoring.yaml"
        assert feat.name is None
        assert feat.labels == []

    def test_uri_with_name_override_is_valid(self):
        """uri + explicit name is a valid reference with an identifier."""
        feat = Feature.model_validate(
            {"name": "my-monitoring", "uri": "./features/monitoring.yaml"}
        )
        assert feat.name == "my-monitoring"
        assert feat.uri == "./features/monitoring.yaml"

    def test_uri_with_full_inline_fields_is_valid(self):
        """uri + inline labels keeps the inline labels (loader merge scenario)."""
        feat = Feature.model_validate(
            {
                "name": "monitoring",
                "uri": "./features/monitoring.yaml",
                "labels": ["gpu"],
                "install-cmds": ["echo gpu"],
            }
        )
        assert feat.labels == ["gpu"]
        assert feat.install_cmds == ["echo gpu"]

    def test_uri_stored_on_resolved_feature(self):
        """The uri field is preserved after resolution for transparency."""
        feat = Feature.model_validate(
            {
                "name": "monitoring",
                "uri": "./features/monitoring.yaml",
                "labels": ["primary"],
            }
        )
        assert feat.uri == "./features/monitoring.yaml"


# ---------------------------------------------------------------------------
# Cluster
# ---------------------------------------------------------------------------


class TestCluster:
    def _minimal(self) -> dict:
        return {
            "name": "test-cluster",
            "hosts": [
                {"name": "ctrl", "uri": "10.0.0.1", "labels": ["controller"]}
            ],
        }

    def test_valid_minimal(self):
        cluster = Cluster.model_validate(self._minimal())
        assert cluster.name == "test-cluster"
        assert cluster.version == "v1"
        assert len(cluster.hosts) == 1
        assert cluster.features == []

    def test_no_hosts_fails(self):
        with pytest.raises(ValidationError):
            Cluster.model_validate({"name": "empty", "hosts": []})

    def test_no_controller_label_fails(self):
        with pytest.raises(ValidationError):
            Cluster.model_validate(
                {
                    "name": "bad",
                    "hosts": [
                        {"name": "w1", "uri": "10.0.0.2", "labels": ["worker"]}
                    ],
                }
            )

    def test_sole_label_satisfies_controller_requirement(self):
        cluster = Cluster.model_validate(
            {
                "name": "sole",
                "hosts": [
                    {"name": "s1", "uri": "10.0.0.1", "labels": ["sole"]}
                ],
            }
        )
        assert cluster.name == "sole"

    def test_controller_plus_extra_labels(self):
        cluster = Cluster.model_validate(
            {
                "name": "c",
                "hosts": [
                    {
                        "name": "ctrl",
                        "uri": "10.0.0.1",
                        "labels": ["controller", "primary", "monitoring"],
                    }
                ],
            }
        )
        assert cluster.hosts[0].has_label("primary")

    def test_duplicate_uris_fail(self):
        with pytest.raises(ValidationError):
            Cluster.model_validate(
                {
                    "name": "dup",
                    "hosts": [
                        {"name": "a", "uri": "10.0.0.1", "labels": ["controller"]},
                        {"name": "b", "uri": "10.0.0.1", "labels": ["worker"]},
                    ],
                }
            )

    def test_duplicate_feature_names_fail(self):
        with pytest.raises(ValidationError):
            Cluster.model_validate(
                {
                    "name": "dup",
                    "hosts": [
                        {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]}
                    ],
                    "features": [
                        {"name": "mon", "labels": ["x"]},
                        {"name": "mon", "labels": ["y"]},
                    ],
                }
            )

    def test_duplicate_feature_uris_fail(self):
        """Two URI-only entries pointing to the same file must be rejected."""
        with pytest.raises(ValidationError):
            Cluster.model_validate(
                {
                    "name": "dup-uri",
                    "hosts": [
                        {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]}
                    ],
                    "features": [
                        {"uri": "./features/monitoring.yaml"},
                        {"uri": "./features/monitoring.yaml"},
                    ],
                }
            )

    def test_non_controller_host_with_custom_labels_only(self):
        """Non-controller nodes need no reserved labels; they're workers by topology."""
        cluster = Cluster.model_validate(
            {
                "name": "free-label",
                "hosts": [
                    {"name": "ctrl", "uri": "10.0.0.1", "labels": ["controller"]},
                    {"name": "n1", "uri": "10.0.0.2", "labels": ["compute", "gpu"]},
                    {"name": "n2", "uri": "10.0.0.3", "labels": ["storage", "high-io"]},
                ],
            }
        )
        assert len(cluster.hosts) == 3

    def test_no_sole_field_round_trips(self):
        """no-sole is preserved on the model."""
        cluster = Cluster.model_validate(
            {
                "name": "no-sole-cluster",
                "hosts": [
                    {
                        "name": "ctrl",
                        "uri": "10.0.0.1",
                        "labels": ["controller"],
                        "no-sole": True,
                    }
                ],
            }
        )
        assert cluster.hosts[0].no_sole is True

    def test_version_default(self):
        cluster = Cluster.model_validate(self._minimal())
        assert cluster.version == "v1"

    def test_version_custom(self):
        data = self._minimal()
        data["version"] = "v2"
        cluster = Cluster.model_validate(data)
        assert cluster.version == "v2"

    def test_config_paths(self):
        data = self._minimal()
        data["config"] = ["./config/settings.yaml"]
        cluster = Cluster.model_validate(data)
        assert cluster.config == ["./config/settings.yaml"]

    def test_full_cluster(self):
        cluster = Cluster.model_validate(
            {
                "name": "full",
                "hosts": [
                    {
                        "name": "ctrl",
                        "uri": "10.0.0.1",
                        "labels": ["controller", "primary"],
                    },
                    {
                        "name": "wrk",
                        "uri": "10.0.0.2",
                        "labels": ["worker", "compute"],
                    },
                ],
                "features": [
                    {"name": "monitoring", "labels": ["primary", "compute"]},
                ],
            }
        )
        assert len(cluster.hosts) == 2
        assert len(cluster.features) == 1


# ---------------------------------------------------------------------------
# BackendConfig
# ---------------------------------------------------------------------------


class TestBackendConfig:
    """Tests for the BackendConfig model."""

    def test_default_is_empty(self):
        from zcc.models.backend import BackendConfig

        cfg = BackendConfig()
        assert cfg.arguments == {}
        assert cfg.config_files == {}

    def test_arguments_are_stored(self):
        from zcc.models.backend import BackendConfig

        cfg = BackendConfig.model_validate({"arguments": {"--network": "calico"}})
        assert cfg.arguments == {"--network": "calico"}

    def test_config_file_inline_content(self):
        from zcc.models.backend import BackendConfig

        cfg = BackendConfig.model_validate(
            {"k0s.yaml": "apiVersion: k0s.k0sproject.io/v1beta1\nkind: ClusterConfig\n"}
        )
        assert cfg.config_files == {
            "k0s.yaml": "apiVersion: k0s.k0sproject.io/v1beta1\nkind: ClusterConfig\n"
        }

    def test_config_file_non_string_value_raises(self):
        from pydantic import ValidationError
        from zcc.models.backend import BackendConfig

        with pytest.raises(ValidationError):
            BackendConfig.model_validate({"k0s.yaml": {"nested": "dict"}})

    def test_cluster_backend_defaults_to_empty(self):
        """Cluster.backend defaults to an empty BackendConfig when omitted."""
        cluster = Cluster.model_validate(
            {
                "name": "t",
                "hosts": [{"name": "h", "uri": "10.0.0.1", "labels": ["controller"]}],
            }
        )
        assert cluster.backend.arguments == {}
        assert cluster.backend.config_files == {}

    def test_cluster_backend_parses_arguments_and_files(self):
        cluster = Cluster.model_validate(
            {
                "name": "t",
                "hosts": [{"name": "h", "uri": "10.0.0.1", "labels": ["controller"]}],
                "backend": {
                    "arguments": {"--network": "calico"},
                    "k0s.yaml": "apiVersion: k0s.k0sproject.io/v1beta1\n",
                },
            }
        )
        assert cluster.backend.arguments == {"--network": "calico"}
        assert "k0s.yaml" in cluster.backend.config_files
