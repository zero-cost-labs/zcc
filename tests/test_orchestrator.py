"""Tests for DeployOrchestrator.plan() and target resolution logic."""

from __future__ import annotations

import pytest

from zcc.deploy.orchestrator import DeployOrchestrator
from zcc.models.cluster import Cluster


def _make_cluster(raw: dict) -> Cluster:
    return Cluster.model_validate(raw)


class TestPlan:
    def test_plan_lists_all_hosts(self):
        cluster = _make_cluster(
            {
                "name": "plan-test",
                "hosts": [
                    {"name": "ctrl", "uri": "10.0.0.1", "roles": ["controller"], "labels": ["primary"]},
                    {"name": "w1", "uri": "10.0.0.2", "roles": ["worker"], "labels": ["compute"]},
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
                    {"name": "ctrl", "uri": "10.0.0.1", "roles": ["controller"], "labels": ["primary"]},
                    {"name": "w1", "uri": "10.0.0.2", "roles": ["worker"], "labels": ["compute"]},
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


class TestResolveTargets:
    def _orchestrator(self, hosts, features=None) -> DeployOrchestrator:
        data: dict = {
            "name": "t",
            "hosts": hosts,
        }
        if features:
            data["features"] = features
        return DeployOrchestrator(_make_cluster(data))

    def test_label_match(self):
        orch = self._orchestrator(
            hosts=[
                {"name": "a", "uri": "10.0.0.1", "roles": ["controller"], "labels": ["alpha"]},
                {"name": "b", "uri": "10.0.0.2", "roles": ["worker"], "labels": ["beta"]},
            ]
        )
        from zcc.models.feature import Feature

        feat = Feature.model_validate({"name": "f", "labels": ["alpha"]})
        targets = orch._resolve_targets(feat)
        assert len(targets) == 1
        assert targets[0].name == "a"

    def test_role_match(self):
        orch = self._orchestrator(
            hosts=[
                {"name": "ctrl", "uri": "10.0.0.1", "roles": ["controller"]},
                {"name": "wrk", "uri": "10.0.0.2", "roles": ["worker"]},
            ]
        )
        from zcc.models.feature import Feature

        feat = Feature.model_validate({"name": "f", "roles": ["worker"]})
        targets = orch._resolve_targets(feat)
        assert len(targets) == 1
        assert targets[0].name == "wrk"

    def test_no_match_returns_empty(self):
        orch = self._orchestrator(
            hosts=[
                {"name": "a", "uri": "10.0.0.1", "roles": ["controller"], "labels": ["x"]},
            ]
        )
        from zcc.models.feature import Feature

        feat = Feature.model_validate({"name": "f", "labels": ["z"]})
        targets = orch._resolve_targets(feat)
        assert targets == []

    def test_label_and_role_match_no_duplicates(self):
        """A host matching both label and role should appear only once."""
        orch = self._orchestrator(
            hosts=[
                {
                    "name": "sole",
                    "uri": "10.0.0.1",
                    "roles": ["sole"],
                    "labels": ["all"],
                },
            ]
        )
        from zcc.models.feature import Feature

        feat = Feature.model_validate(
            {"name": "f", "labels": ["all"], "roles": ["sole"]}
        )
        targets = orch._resolve_targets(feat)
        # Label check fires first; role check won't add duplicate.
        assert len(targets) == 1
