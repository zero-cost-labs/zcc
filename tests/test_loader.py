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
        """A cluster without a controller is now valid (DRAFT state)."""
        from zcc.models.state import ClusterState

        cluster = load_cluster(FIXTURES / "invalid-no-controller.yaml")
        assert cluster.state is ClusterState.DRAFT

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


# ---------------------------------------------------------------------------
# Feature reference resolution
# ---------------------------------------------------------------------------


class TestFeatureRefResolution:
    """Tests that verify URI and name-based feature references are resolved."""

    def test_uri_reference_loads_feature(self):
        """A feature entry with only `uri` is resolved from the referenced file."""
        cluster = load_cluster(FIXTURES / "valid-cluster-feature-refs.yaml")
        monitoring = next(f for f in cluster.features if f.name == "monitoring")
        assert set(monitoring.labels) == {"primary", "compute"}

    def test_name_only_reference_loads_feature(self):
        """A feature entry with only `name` is resolved via features/<name>.yaml."""
        cluster = load_cluster(FIXTURES / "valid-cluster-feature-refs.yaml")
        storage = next(f for f in cluster.features if f.name == "storage-backend")
        assert "storage" in storage.labels

    def test_resolved_feature_count_matches(self):
        """All feature references are resolved; the cluster has the right count."""
        cluster = load_cluster(FIXTURES / "valid-cluster-feature-refs.yaml")
        assert len(cluster.features) == 2

    def test_uri_reference_preserves_uri_field(self):
        """The uri field is preserved on the resolved Feature for transparency."""
        cluster = load_cluster(FIXTURES / "valid-cluster-feature-refs.yaml")
        monitoring = next(f for f in cluster.features if f.name == "monitoring")
        assert monitoring.uri is not None
        assert "monitoring" in monitoring.uri

    def test_inline_name_override_wins_over_file_name(self, tmp_path):
        """When a URI entry also gives a name, that name overrides the file's name."""
        feat_file = tmp_path / "prometheus.yaml"
        feat_file.write_text(
            "name: prometheus\n"
            "labels: [monitoring]\n"
            "install-cmds:\n"
            "  - echo install\n"
        )
        cfg = tmp_path / "cluster.yaml"
        cfg.write_text(
            "name: test\n"
            "hosts:\n"
            "  - name: ctrl\n"
            "    uri: 10.0.0.1\n"
            "    labels: [controller]\n"
            "features:\n"
            "  - name: my-prometheus\n"
            f"    uri: {feat_file}\n"
        )
        cluster = load_cluster(cfg)
        assert cluster.features[0].name == "my-prometheus"
        assert "monitoring" in cluster.features[0].labels

    def test_inline_labels_appended_to_file_labels(self, tmp_path):
        """Inline labels are appended to the file's labels, not replacing them."""
        feat_file = tmp_path / "base-feature.yaml"
        feat_file.write_text(
            "name: base\n"
            "labels: [worker]\n"
            "install-cmds:\n"
            "  - echo install\n"
        )
        cfg = tmp_path / "cluster.yaml"
        cfg.write_text(
            "name: test\n"
            "hosts:\n"
            "  - name: ctrl\n"
            "    uri: 10.0.0.1\n"
            "    labels: [controller]\n"
            "features:\n"
            "  - uri: " + str(feat_file) + "\n"
            "    labels: [gpu]\n"
        )
        cluster = load_cluster(cfg)
        assert cluster.features[0].labels == ["worker", "gpu"]

    def test_inline_label_negation_removes_file_label(self, tmp_path):
        """A label starting with '-' removes the matching label from the file."""
        feat_file = tmp_path / "base-feature.yaml"
        feat_file.write_text(
            "name: base\n"
            "labels: [worker, storage]\n"
            "install-cmds:\n"
            "  - echo install\n"
        )
        cfg = tmp_path / "cluster.yaml"
        cfg.write_text(
            "name: test\n"
            "hosts:\n"
            "  - name: ctrl\n"
            "    uri: 10.0.0.1\n"
            "    labels: [controller]\n"
            "features:\n"
            "  - uri: " + str(feat_file) + "\n"
            "    labels: [-worker]\n"
        )
        cluster = load_cluster(cfg)
        assert cluster.features[0].labels == ["storage"]

    def test_inline_labels_append_and_negate_mixed(self, tmp_path):
        """Labels can be added and removed in the same cluster-level entry."""
        feat_file = tmp_path / "base-feature.yaml"
        feat_file.write_text(
            "name: base\n"
            "labels: [worker, storage, compute]\n"
            "install-cmds:\n"
            "  - echo install\n"
        )
        cfg = tmp_path / "cluster.yaml"
        cfg.write_text(
            "name: test\n"
            "hosts:\n"
            "  - name: ctrl\n"
            "    uri: 10.0.0.1\n"
            "    labels: [controller]\n"
            "features:\n"
            "  - uri: " + str(feat_file) + "\n"
            "    labels: [-storage, gpu]\n"
        )
        cluster = load_cluster(cfg)
        assert cluster.features[0].labels == ["worker", "compute", "gpu"]

    def test_uri_reference_missing_file_raises_config_error(self, tmp_path):
        """A URI reference to a non-existent file raises ConfigError."""
        cfg = tmp_path / "cluster.yaml"
        cfg.write_text(
            "name: test\n"
            "hosts:\n"
            "  - name: ctrl\n"
            "    uri: 10.0.0.1\n"
            "    labels: [controller]\n"
            "features:\n"
            "  - uri: ./missing-feature.yaml\n"
        )
        with pytest.raises(ConfigError, match="Cannot read feature file"):
            load_cluster(cfg)

    def test_name_only_missing_features_dir_raises_config_error(self, tmp_path):
        """A name-only reference with no features/<name>.yaml raises ConfigError."""
        cfg = tmp_path / "cluster.yaml"
        cfg.write_text(
            "name: test\n"
            "hosts:\n"
            "  - name: ctrl\n"
            "    uri: 10.0.0.1\n"
            "    labels: [controller]\n"
            "features:\n"
            "  - name: nonexistent-feature\n"
        )
        with pytest.raises(ConfigError, match="Cannot read feature file"):
            load_cluster(cfg)

    def test_uri_reference_invalid_yaml_raises_config_error(self, tmp_path):
        """A URI pointing to invalid YAML raises ConfigError."""
        feat_file = tmp_path / "bad.yaml"
        feat_file.write_text(":\nbad: [yaml: {broken")
        cfg = tmp_path / "cluster.yaml"
        cfg.write_text(
            "name: test\n"
            "hosts:\n"
            "  - name: ctrl\n"
            "    uri: 10.0.0.1\n"
            "    labels: [controller]\n"
            f"features:\n"
            f"  - uri: {feat_file}\n"
        )
        with pytest.raises(ConfigError, match="Invalid YAML in feature file"):
            load_cluster(cfg)

    def test_uri_reference_non_mapping_yaml_raises_config_error(self, tmp_path):
        """A URI pointing to a non-mapping YAML raises ConfigError."""
        feat_file = tmp_path / "list.yaml"
        feat_file.write_text("- item1\n- item2\n")
        cfg = tmp_path / "cluster.yaml"
        cfg.write_text(
            "name: test\n"
            "hosts:\n"
            "  - name: ctrl\n"
            "    uri: 10.0.0.1\n"
            "    labels: [controller]\n"
            f"features:\n"
            f"  - uri: {feat_file}\n"
        )
        with pytest.raises(ConfigError, match="must be a YAML mapping"):
            load_cluster(cfg)

    def test_inline_feature_still_works(self, tmp_path):
        """Fully-inline features are unaffected by the resolution logic."""
        cfg = tmp_path / "cluster.yaml"
        cfg.write_text(
            "name: test\n"
            "hosts:\n"
            "  - name: ctrl\n"
            "    uri: 10.0.0.1\n"
            "    labels: [controller]\n"
            "features:\n"
            "  - name: myapp\n"
            "    labels: [worker]\n"
            "    install-cmds:\n"
            "      - echo hi\n"
        )
        cluster = load_cluster(cfg)
        assert cluster.features[0].name == "myapp"
        assert cluster.features[0].labels == ["worker"]
        assert cluster.features[0].install_cmds == ["echo hi"]

    def test_name_only_ref_via_features_dir_convention(self, tmp_path):
        """A name-only entry loads from features/<name>.yaml next to the cluster."""
        features_dir = tmp_path / "features"
        features_dir.mkdir()
        (features_dir / "grafana.yaml").write_text(
            "name: grafana\n"
            "labels: [monitoring]\n"
            "install-cmds:\n"
            "  - echo grafana\n"
        )
        cfg = tmp_path / "cluster.yaml"
        cfg.write_text(
            "name: test\n"
            "hosts:\n"
            "  - name: ctrl\n"
            "    uri: 10.0.0.1\n"
            "    labels: [controller]\n"
            "features:\n"
            "  - name: grafana\n"
        )
        cluster = load_cluster(cfg)
        assert cluster.features[0].name == "grafana"
        assert "monitoring" in cluster.features[0].labels
