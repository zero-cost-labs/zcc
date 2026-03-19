"""Tests for DeployOrchestrator.plan() and target resolution logic."""

from __future__ import annotations

import pytest

from zcc.deploy.orchestrator import DeployOrchestrator
from zcc.models.cluster import Cluster
from zcc.models.feature import Feature


def _make_cluster(raw: dict) -> Cluster:
    return Cluster.model_validate(raw)


class TestPlan:
    def test_plan_lists_all_hosts(self):
        cluster = _make_cluster(
            {
                "name": "plan-test",
                "hosts": [
                    {"name": "ctrl", "uri": "10.0.0.1", "labels": ["controller", "primary"]},
                    {"name": "w1", "uri": "10.0.0.2", "labels": ["worker", "compute"]},
                ],
            }
        )
        lines = DeployOrchestrator(cluster).plan()
        output = "\n".join(lines)
        assert "ctrl" in output
        assert "w1" in output

    def test_plan_lists_features_with_targets(self):
        cluster = _make_cluster(
            {
                "name": "plan-feat",
                "hosts": [
                    {"name": "ctrl", "uri": "10.0.0.1", "labels": ["controller", "primary"]},
                    {"name": "w1", "uri": "10.0.0.2", "labels": ["worker", "compute"]},
                ],
                "features": [
                    {"name": "mon", "labels": ["primary"]},
                ],
            }
        )
        lines = DeployOrchestrator(cluster).plan()
        output = "\n".join(lines)
        assert "mon" in output
        assert "ctrl" in output  # ctrl has label 'primary'

    def test_plan_shows_all_labels(self):
        cluster = _make_cluster(
            {
                "name": "labels-test",
                "hosts": [
                    {"name": "ctrl", "uri": "10.0.0.1", "labels": ["controller", "primary", "gpu"]},
                ],
            }
        )
        output = "\n".join(DeployOrchestrator(cluster).plan())
        assert "primary" in output
        assert "gpu" in output


class TestSplitHosts:
    def _orch(self, hosts) -> DeployOrchestrator:
        return DeployOrchestrator(_make_cluster({"name": "t", "hosts": hosts}))

    def test_controller_label_goes_to_controllers(self):
        orch = self._orch([
            {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "w", "uri": "10.0.0.2", "labels": ["worker"]},
        ])
        controllers, workers = orch._split_hosts()
        assert len(controllers) == 1
        assert controllers[0].name == "c"
        assert len(workers) == 1
        assert workers[0].name == "w"

    def test_sole_label_goes_to_controllers_only(self):
        orch = self._orch([
            {"name": "s", "uri": "10.0.0.1", "labels": ["sole", "dev"]},
        ])
        controllers, workers = orch._split_hosts()
        assert len(controllers) == 1
        # sole nodes do NOT appear as pure workers
        assert workers == []

    def test_worker_with_extra_labels(self):
        orch = self._orch([
            {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "w", "uri": "10.0.0.2", "labels": ["worker", "compute", "gpu"]},
        ])
        _, workers = orch._split_hosts()
        assert workers[0].name == "w"


class TestResolveTargets:
    def _orch(self, hosts) -> DeployOrchestrator:
        return DeployOrchestrator(_make_cluster({"name": "t", "hosts": hosts}))

    def test_label_match(self):
        orch = self._orch([
            {"name": "a", "uri": "10.0.0.1", "labels": ["controller", "alpha"]},
            {"name": "b", "uri": "10.0.0.2", "labels": ["worker", "beta"]},
        ])
        feat = Feature.model_validate({"name": "f", "labels": ["alpha"]})
        targets = orch._resolve_targets(feat)
        assert len(targets) == 1
        assert targets[0].name == "a"

    def test_reserved_label_match(self):
        orch = self._orch([
            {"name": "ctrl", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "wrk", "uri": "10.0.0.2", "labels": ["worker"]},
        ])
        feat = Feature.model_validate({"name": "f", "labels": ["worker"]})
        targets = orch._resolve_targets(feat)
        assert len(targets) == 1
        assert targets[0].name == "wrk"

    def test_no_match_returns_empty(self):
        orch = self._orch([
            {"name": "a", "uri": "10.0.0.1", "labels": ["controller", "x"]},
        ])
        feat = Feature.model_validate({"name": "f", "labels": ["z"]})
        assert orch._resolve_targets(feat) == []

    def test_multi_label_feature_matches_multiple_hosts(self):
        orch = self._orch([
            {"name": "ctrl", "uri": "10.0.0.1", "labels": ["controller", "primary"]},
            {"name": "wrk", "uri": "10.0.0.2", "labels": ["worker", "compute"]},
        ])
        feat = Feature.model_validate({"name": "f", "labels": ["primary", "compute"]})
        targets = orch._resolve_targets(feat)
        assert len(targets) == 2

    def test_no_duplicate_when_host_matches_multiple_feature_labels(self):
        """A host that carries multiple labels from a single feature is returned once."""
        orch = self._orch([
            {"name": "h", "uri": "10.0.0.1", "labels": ["controller", "primary", "compute"]},
        ])
        feat = Feature.model_validate({"name": "f", "labels": ["primary", "compute"]})
        targets = orch._resolve_targets(feat)
        assert len(targets) == 1
