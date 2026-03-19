"""Tests for the YAML config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from zcc.loader import ConfigError, load_cluster
from zcc.models.host import HostRole

FIXTURES = Path(__file__).parent / "fixtures"


class TestLoadCluster:
    def test_valid_cluster(self):
        cluster = load_cluster(FIXTURES / "valid-cluster.yaml")
        assert cluster.name == "valid-cluster"
        assert cluster.version == "v1"
        assert len(cluster.hosts) == 3
        assert len(cluster.features) == 2

    def test_valid_cluster_host_roles(self):
        cluster = load_cluster(FIXTURES / "valid-cluster.yaml")
        ctrl = next(h for h in cluster.hosts if h.name == "ctrl")
        assert HostRole.CONTROLLER in ctrl.roles
        assert "primary" in ctrl.labels

    def test_valid_cluster_feature_targets(self):
        cluster = load_cluster(FIXTURES / "valid-cluster.yaml")
        monitoring = next(f for f in cluster.features if f.name == "monitoring")
        assert "primary" in monitoring.labels or "compute" in monitoring.labels

    def test_invalid_no_roles(self):
        with pytest.raises(ConfigError):
            load_cluster(FIXTURES / "invalid-no-roles.yaml")

    def test_invalid_no_controller(self):
        with pytest.raises(ConfigError):
            load_cluster(FIXTURES / "invalid-no-controller.yaml")

    def test_file_not_found(self):
        with pytest.raises(ConfigError, match="Cannot read"):
            load_cluster("/nonexistent/path/cluster.yaml")

    def test_invalid_yaml(self, tmp_path):
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(":\ninvalid: [yaml: {broken")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            load_cluster(bad_yaml)

    def test_non_mapping_yaml(self, tmp_path):
        list_yaml = tmp_path / "list.yaml"
        list_yaml.write_text("- item1\n- item2\n")
        with pytest.raises(ConfigError, match="must be a YAML mapping"):
            load_cluster(list_yaml)

    def test_load_from_string_path(self):
        cluster = load_cluster(str(FIXTURES / "valid-cluster.yaml"))
        assert cluster.name == "valid-cluster"

    def test_sole_node_example(self, tmp_path):
        cfg = tmp_path / "sole.yaml"
        cfg.write_text(
            "name: sole\n"
            "hosts:\n"
            "  - name: node\n"
            "    uri: 10.0.0.1\n"
            "    roles: [sole]\n"
        )
        cluster = load_cluster(cfg)
        assert HostRole.SOLE in cluster.hosts[0].roles
