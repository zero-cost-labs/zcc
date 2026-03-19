"""Tests for the YAML config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from zcc.loader import ConfigError, load_cluster

FIXTURES = Path(__file__).parent / "fixtures"


class TestLoadCluster:
    def test_valid_cluster(self):
        cluster = load_cluster(FIXTURES / "valid-cluster.yaml")
        assert cluster.name == "valid-cluster"
        assert cluster.version == "v1"
        assert len(cluster.hosts) == 3
        assert len(cluster.features) == 2

    def test_valid_cluster_host_labels(self):
        cluster = load_cluster(FIXTURES / "valid-cluster.yaml")
        ctrl = next(h for h in cluster.hosts if h.name == "ctrl")
        assert "controller" in ctrl.labels
        assert "primary" in ctrl.labels

    def test_valid_cluster_feature_targets(self):
        cluster = load_cluster(FIXTURES / "valid-cluster.yaml")
        monitoring = next(f for f in cluster.features if f.name == "monitoring")
        assert bool({"primary", "compute"} & set(monitoring.labels))

    def test_invalid_no_labels(self):
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

    def test_sole_label_example(self, tmp_path):
        cfg = tmp_path / "sole.yaml"
        cfg.write_text(
            "name: sole\n"
            "hosts:\n"
            "  - name: node\n"
            "    uri: 10.0.0.1\n"
            "    labels: [sole]\n"
        )
        cluster = load_cluster(cfg)
        assert "sole" in cluster.hosts[0].labels

    def test_no_sole_field_loaded(self, tmp_path):
        """no-sole: true is preserved after loading."""
        cfg = tmp_path / "no-sole.yaml"
        cfg.write_text(
            "name: no-sole\n"
            "hosts:\n"
            "  - name: ctrl\n"
            "    uri: 10.0.0.1\n"
            "    labels: [controller]\n"
            "    no-sole: true\n"
        )
        cluster = load_cluster(cfg)
        assert cluster.hosts[0].no_sole is True

    def test_custom_labels_on_non_controller(self, tmp_path):
        """Non-controller nodes may carry only user-defined labels."""
        cfg = tmp_path / "custom.yaml"
        cfg.write_text(
            "name: custom\n"
            "hosts:\n"
            "  - name: ctrl\n"
            "    uri: 10.0.0.1\n"
            "    labels: [controller]\n"
            "  - name: compute\n"
            "    uri: 10.0.0.2\n"
            "    labels: [gpu, high-memory]\n"
        )
        cluster = load_cluster(cfg)
        compute = next(h for h in cluster.hosts if h.name == "compute")
        assert "gpu" in compute.labels
        assert compute.no_sole is False
