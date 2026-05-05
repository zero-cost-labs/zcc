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

    def test_plan_shows_effective_role(self):
        """Plan output prefixes each host with its effective k0s role."""
        cluster = _make_cluster(
            {
                "name": "role-test",
                "hosts": [
                    {"name": "ctrl", "uri": "10.0.0.1", "labels": ["controller"]},
                    {"name": "wrk", "uri": "10.0.0.2", "labels": ["compute"]},
                ],
            }
        )
        output = "\n".join(DeployOrchestrator(cluster).plan())
        assert "[controller]" in output
        assert "[worker]" in output

    def test_plan_shows_sole_role_for_single_controller(self):
        """A lone controller is auto-promoted to sole in the plan."""
        cluster = _make_cluster(
            {
                "name": "sole-test",
                "hosts": [
                    {"name": "ctrl", "uri": "10.0.0.1", "labels": ["controller"]},
                ],
            }
        )
        output = "\n".join(DeployOrchestrator(cluster).plan())
        assert "[sole]" in output

    def test_plan_shows_controller_role_when_no_sole(self):
        """A controller with no-sole:true shows as [controller] even when alone."""
        cluster = _make_cluster(
            {
                "name": "no-sole-test",
                "hosts": [
                    {"name": "ctrl", "uri": "10.0.0.1", "labels": ["controller"], "no-sole": True},
                ],
            }
        )
        output = "\n".join(DeployOrchestrator(cluster).plan())
        assert "[controller]" in output
        assert "[sole]" not in output

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

    def test_plan_shows_absorption_annotation(self):
        """When a feature is absorbed by sole, plan says so."""
        cluster = _make_cluster(
            {
                "name": "absorption-test",
                "hosts": [
                    {"name": "ctrl", "uri": "10.0.0.1", "labels": ["controller"]},
                ],
                "features": [
                    {"name": "exotic", "labels": ["gpu"]},
                ],
            }
        )
        output = "\n".join(DeployOrchestrator(cluster).plan())
        assert "exotic" in output
        assert "absorbed by sole" in output

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

    def test_plan_shows_singleton_annotation(self):
        """Plan output marks singleton features with [singleton]."""
        cluster = _make_cluster(
            {
                "name": "singleton-test",
                "hosts": [
                    {"name": "a", "uri": "10.0.0.1", "labels": ["controller", "compute"]},
                    {"name": "b", "uri": "10.0.0.2", "labels": ["worker", "compute"]},
                ],
                "features": [
                    {"name": "db", "labels": ["compute"], "singleton": True},
                ],
            }
        )
        output = "\n".join(DeployOrchestrator(cluster).plan())
        assert "[singleton]" in output
        # Only the first matching host should appear as target
        assert "a" in output

    def test_plan_no_singleton_annotation_for_normal_feature(self):
        """Plan output does not mark normal (non-singleton) features."""
        cluster = _make_cluster(
            {
                "name": "normal-test",
                "hosts": [
                    {"name": "w1", "uri": "10.0.0.1", "labels": ["controller", "compute"]},
                ],
                "features": [
                    {"name": "app", "labels": ["compute"]},
                ],
            }
        )
        output = "\n".join(DeployOrchestrator(cluster).plan())
        assert "[singleton]" not in output


class TestSplitHosts:
    def _orch(self, hosts) -> DeployOrchestrator:
        return DeployOrchestrator(_make_cluster({"name": "t", "hosts": hosts}))

    def test_controller_label_goes_to_controllers(self):
        orch = self._orch([
            {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "w", "uri": "10.0.0.2", "labels": ["worker"]},
        ])
        controllers, non_controllers = orch._split_hosts()
        assert len(controllers) == 1
        assert controllers[0].name == "c"
        assert len(non_controllers) == 1
        assert non_controllers[0].name == "w"

    def test_sole_label_goes_to_controllers_only(self):
        orch = self._orch([
            {"name": "s", "uri": "10.0.0.1", "labels": ["sole", "dev"]},
        ])
        controllers, non_controllers = orch._split_hosts()
        assert len(controllers) == 1
        # sole nodes do NOT appear as non-controllers
        assert non_controllers == []

    def test_custom_label_only_goes_to_non_controllers(self):
        """A host with only a user-defined label (no controller/sole) is a non-controller."""
        orch = self._orch([
            {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "n", "uri": "10.0.0.2", "labels": ["compute", "gpu"]},
        ])
        controllers, non_controllers = orch._split_hosts()
        assert len(non_controllers) == 1
        assert non_controllers[0].name == "n"

    def test_worker_with_extra_labels(self):
        orch = self._orch([
            {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "w", "uri": "10.0.0.2", "labels": ["worker", "compute", "gpu"]},
        ])
        _, non_controllers = orch._split_hosts()
        assert non_controllers[0].name == "w"

    def test_all_controllers_no_non_controllers(self):
        orch = self._orch([
            {"name": "c1", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "c2", "uri": "10.0.0.2", "labels": ["controller"]},
        ])
        controllers, non_controllers = orch._split_hosts()
        assert len(controllers) == 2
        assert non_controllers == []


class TestIsSole:
    def _orch(self, hosts) -> DeployOrchestrator:
        return DeployOrchestrator(_make_cluster({"name": "t", "hosts": hosts}))

    def test_single_controller_is_auto_sole(self):
        orch = self._orch([
            {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]},
        ])
        host = orch.cluster.hosts[0]
        assert orch._is_sole(host) is True

    def test_controller_with_workers_is_not_sole(self):
        orch = self._orch([
            {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "w", "uri": "10.0.0.2", "labels": ["compute"]},
        ])
        ctrl = next(h for h in orch.cluster.hosts if h.name == "c")
        assert orch._is_sole(ctrl) is False

    def test_explicit_sole_label_always_sole(self):
        """A host with explicit 'sole' is sole regardless of other nodes."""
        orch = self._orch([
            {"name": "s", "uri": "10.0.0.1", "labels": ["sole"]},
            {"name": "w", "uri": "10.0.0.2", "labels": ["compute"]},
        ])
        sole_host = next(h for h in orch.cluster.hosts if h.name == "s")
        assert orch._is_sole(sole_host) is True

    def test_no_sole_flag_prevents_auto_sole(self):
        """A controller with no-sole:true is never sole, even when alone."""
        orch = self._orch([
            {"name": "c", "uri": "10.0.0.1", "labels": ["controller"], "no-sole": True},
        ])
        host = orch.cluster.hosts[0]
        assert orch._is_sole(host) is False

    def test_no_sole_flag_in_all_controller_cluster(self):
        """In an all-controller cluster, no-sole nodes stay pure controllers."""
        orch = self._orch([
            {"name": "c1", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "c2", "uri": "10.0.0.2", "labels": ["controller"], "no-sole": True},
        ])
        c1 = next(h for h in orch.cluster.hosts if h.name == "c1")
        c2 = next(h for h in orch.cluster.hosts if h.name == "c2")
        assert orch._is_sole(c1) is True   # auto-sole: no non-controllers
        assert orch._is_sole(c2) is False   # opted out


class TestSoleHosts:
    def _orch(self, hosts) -> DeployOrchestrator:
        return DeployOrchestrator(_make_cluster({"name": "t", "hosts": hosts}))

    def test_single_controller_sole(self):
        orch = self._orch([{"name": "c", "uri": "10.0.0.1", "labels": ["controller"]}])
        assert len(orch._sole_hosts()) == 1

    def test_all_controllers_all_sole(self):
        orch = self._orch([
            {"name": "c1", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "c2", "uri": "10.0.0.2", "labels": ["controller"]},
        ])
        assert len(orch._sole_hosts()) == 2

    def test_mixed_cluster_no_sole_hosts(self):
        """When non-controller nodes exist, no controller auto-becomes sole."""
        orch = self._orch([
            {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "w", "uri": "10.0.0.2", "labels": ["compute"]},
        ])
        assert orch._sole_hosts() == []

    def test_no_sole_opt_out_excluded(self):
        """Controllers with no-sole:true are excluded from sole_hosts."""
        orch = self._orch([
            {"name": "c1", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "c2", "uri": "10.0.0.2", "labels": ["controller"], "no-sole": True},
        ])
        sole = orch._sole_hosts()
        assert len(sole) == 1
        assert sole[0].name == "c1"


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

    def test_no_match_in_mixed_cluster_returns_empty(self):
        """No label match in a mixed cluster (controller has workers) → empty."""
        orch = self._orch([
            {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "w", "uri": "10.0.0.2", "labels": ["compute"]},
        ])
        feat = Feature.model_validate({"name": "f", "labels": ["gpu"]})
        assert orch._resolve_targets(feat) == []

    def test_absorption_by_sole_when_no_match(self):
        """Unmatched feature is absorbed by the sole node."""
        orch = self._orch([
            {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]},
        ])
        feat = Feature.model_validate({"name": "f", "labels": ["gpu"]})
        targets = orch._resolve_targets(feat)
        assert len(targets) == 1
        assert targets[0].name == "c"

    def test_absorption_by_multiple_sole_nodes(self):
        """All-controller cluster: every sole node absorbs an unmatched feature."""
        orch = self._orch([
            {"name": "c1", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "c2", "uri": "10.0.0.2", "labels": ["controller"]},
        ])
        feat = Feature.model_validate({"name": "f", "labels": ["exotic"]})
        targets = orch._resolve_targets(feat)
        assert {h.name for h in targets} == {"c1", "c2"}

    def test_no_absorption_when_no_sole_flag(self):
        """A controller with no-sole:true does not absorb unmatched features."""
        orch = self._orch([
            {"name": "c", "uri": "10.0.0.1", "labels": ["controller"], "no-sole": True},
        ])
        feat = Feature.model_validate({"name": "f", "labels": ["gpu"]})
        assert orch._resolve_targets(feat) == []

    def test_explicit_match_takes_precedence_over_absorption(self):
        """When a host explicitly matches, sole absorption does not happen."""
        orch = self._orch([
            {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]},  # auto-sole
            {"name": "g", "uri": "10.0.0.2", "labels": ["gpu"]},
        ])
        # c is NOT sole (g is a non-controller), and g has the label
        feat = Feature.model_validate({"name": "f", "labels": ["gpu"]})
        targets = orch._resolve_targets(feat)
        assert len(targets) == 1
        assert targets[0].name == "g"

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

    def test_singleton_limits_to_first_match(self):
        """singleton=True returns only the first matching host."""
        orch = self._orch([
            {"name": "a", "uri": "10.0.0.1", "labels": ["controller", "compute"]},
            {"name": "b", "uri": "10.0.0.2", "labels": ["worker", "compute"]},
            {"name": "c", "uri": "10.0.0.3", "labels": ["worker", "compute"]},
        ])
        feat = Feature.model_validate({"name": "f", "labels": ["compute"], "singleton": True})
        targets = orch._resolve_targets(feat)
        assert len(targets) == 1
        assert targets[0].name == "a"

    def test_singleton_non_singleton_differs(self):
        """Without singleton all matching hosts are returned."""
        orch = self._orch([
            {"name": "a", "uri": "10.0.0.1", "labels": ["controller", "compute"]},
            {"name": "b", "uri": "10.0.0.2", "labels": ["worker", "compute"]},
        ])
        feat = Feature.model_validate({"name": "f", "labels": ["compute"]})
        targets = orch._resolve_targets(feat)
        assert len(targets) == 2

    def test_singleton_with_sole_absorption(self):
        """singleton=True also limits absorbed sole targets to one."""
        orch = self._orch([
            {"name": "c1", "uri": "10.0.0.1", "labels": ["controller"]},
            {"name": "c2", "uri": "10.0.0.2", "labels": ["controller"]},
        ])
        feat = Feature.model_validate({"name": "f", "labels": ["exotic"], "singleton": True})
        targets = orch._resolve_targets(feat)
        assert len(targets) == 1
        assert targets[0].name == "c1"

    def test_singleton_with_no_match_returns_empty(self):
        """singleton=True on a feature with no matches returns empty list."""
        orch = self._orch([
            {"name": "c", "uri": "10.0.0.1", "labels": ["controller"], "no-sole": True},
        ])
        feat = Feature.model_validate({"name": "f", "labels": ["gpu"], "singleton": True})
        assert orch._resolve_targets(feat) == []


class TestRunParallel:
    """Tests for the parallel executor helper."""

    def _orch(self) -> DeployOrchestrator:
        from zcc.models.host import Host
        cluster = _make_cluster({
            "name": "t",
            "hosts": [{"name": "c", "uri": "10.0.0.1", "labels": ["controller"]}],
        })
        return DeployOrchestrator(cluster)

    def test_empty_list_is_noop(self):
        orch = self._orch()
        # Should not raise
        orch._run_parallel(lambda h: None, [])

    def test_all_items_processed(self):
        orch = self._orch()
        hosts = [
            _make_cluster({
                "name": "t",
                "hosts": [{"name": f"h{i}", "uri": f"10.0.0.{i}", "labels": ["controller"]}],
            }).hosts[0]
            for i in range(1, 4)
        ]
        visited: list[str] = []
        import threading
        lock = threading.Lock()

        def collect(h):
            with lock:
                visited.append(h.name)

        orch._run_parallel(collect, hosts)
        assert sorted(visited) == ["h1", "h2", "h3"]

    def test_single_error_reraises(self):
        orch = self._orch()
        hosts = [orch.cluster.hosts[0]]

        def boom(h):
            raise ValueError("oops")

        with pytest.raises(ValueError, match="oops"):
            orch._run_parallel(boom, hosts)

    def test_multiple_errors_raised_as_runtime_error(self):
        orch = self._orch()
        hosts = [
            _make_cluster({
                "name": "t",
                "hosts": [
                    {"name": f"h{i}", "uri": f"10.0.0.{i}", "labels": ["controller"]}
                    for i in range(1, 3)
                ],
            }).hosts[i]
            for i in range(2)
        ]

        def boom(h):
            raise ValueError(f"fail {h.name}")

        with pytest.raises(RuntimeError, match="2 deployment errors"):
            orch._run_parallel(boom, hosts)


# ---------------------------------------------------------------------------
# State transitions driven by DeployOrchestrator.deploy()
# ---------------------------------------------------------------------------


def _make_dummy_ssh_patcher(monkeypatch):
    """Return a DummySSH class and patch SSHClient with it."""
    class DummySSH:
        def __init__(self, host):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr("zcc.deploy.orchestrator.SSHClient", DummySSH)
    return DummySSH


class TestStateTransitions:
    """Verify that deploy() drives host and feature state transitions."""

    def _deploy_cluster(self, monkeypatch, raw: dict) -> "Cluster":
        """Run a mocked full deployment and return the cluster."""
        cluster = _make_cluster(raw)
        orch = DeployOrchestrator(cluster)

        monkeypatch.setattr(
            DeployOrchestrator, "_run_parallel",
            lambda self, fn, items: [fn(i) for i in items],
        )
        monkeypatch.setattr(DeployOrchestrator, "_install_on", _patched_install_on)
        _make_dummy_ssh_patcher(monkeypatch)

        orch._backend.init_controller = lambda ssh, **kw: ("ctrl-tok", "wrk-tok")
        orch._backend.join_controller = lambda ssh, token: None
        orch._backend.join_worker = lambda ssh, token: None
        orch._backend.enable_worker_scheduling = lambda ssh: None
        orch._features.deploy = lambda ssh, feat: None

        orch.deploy()
        return cluster

    def test_all_hosts_deployed_after_deploy(self, monkeypatch):
        from zcc.models.state import HostState
        cluster = self._deploy_cluster(
            monkeypatch,
            {
                "name": "t",
                "hosts": [
                    {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]},
                    {"name": "w", "uri": "10.0.0.2", "labels": ["worker"]},
                ],
            },
        )
        for host in cluster.hosts:
            assert host.deployment_state is HostState.DEPLOYED

    def test_cluster_state_is_deployed_after_deploy(self, monkeypatch):
        from zcc.models.state import ClusterState
        cluster = self._deploy_cluster(
            monkeypatch,
            {
                "name": "t",
                "hosts": [
                    {"name": "c", "uri": "10.0.0.1", "labels": ["controller"]},
                ],
            },
        )
        assert cluster.state is ClusterState.DEPLOYED

    def test_features_deployed_after_deploy(self, monkeypatch):
        from zcc.models.state import FeatureState
        cluster = self._deploy_cluster(
            monkeypatch,
            {
                "name": "t",
                "hosts": [
                    {"name": "c", "uri": "10.0.0.1", "labels": ["controller", "app"]},
                ],
                "features": [{"name": "app", "labels": ["app"]}],
            },
        )
        assert cluster.features[0].deployment_state is FeatureState.DEPLOYED
        assert "c" in cluster.features[0].deployed_to

    def test_feature_with_no_targets_is_deployed(self, monkeypatch):
        """A feature that has no matching hosts is trivially DEPLOYED."""
        from zcc.models.state import FeatureState
        cluster = self._deploy_cluster(
            monkeypatch,
            {
                "name": "t",
                "hosts": [
                    {"name": "c", "uri": "10.0.0.1", "labels": ["controller"], "no-sole": True},
                ],
                "features": [{"name": "gpu-app", "labels": ["gpu"]}],
            },
        )
        assert cluster.features[0].deployment_state is FeatureState.DEPLOYED

    def test_feature_partial_state_during_multi_host_deploy(self, monkeypatch):
        """deployed_to tracks each host; state becomes DEPLOYED when all done."""
        from zcc.models.state import FeatureState
        cluster = _make_cluster(
            {
                "name": "t",
                "hosts": [
                    {"name": "c", "uri": "10.0.0.1", "labels": ["controller", "app"]},
                    {"name": "w", "uri": "10.0.0.2", "labels": ["worker", "app"]},
                ],
                "features": [{"name": "app", "labels": ["app"]}],
            }
        )
        orch = DeployOrchestrator(cluster)

        monkeypatch.setattr(
            DeployOrchestrator, "_run_parallel",
            lambda self, fn, items: [fn(i) for i in items],
        )
        monkeypatch.setattr(DeployOrchestrator, "_install_on", _patched_install_on)
        _make_dummy_ssh_patcher(monkeypatch)

        orch._backend.init_controller = lambda ssh, **kw: ("ct", "wt")
        orch._backend.join_controller = lambda ssh, token: None
        orch._backend.join_worker = lambda ssh, token: None
        orch._backend.enable_worker_scheduling = lambda ssh: None
        orch._features.deploy = lambda ssh, feat: None

        orch.deploy()

        feat = cluster.features[0]
        assert feat.deployment_state is FeatureState.DEPLOYED
        assert sorted(feat.deployed_to) == ["c", "w"]

    def test_hosts_start_deploying_during_install(self, monkeypatch):
        """Hosts must be DEPLOYING after _install_on, before join steps."""
        from zcc.models.state import HostState
        cluster = _make_cluster(
            {
                "name": "t",
                "hosts": [
                    {"name": "c", "uri": "10.0.0.1", "labels": ["sole"]},
                ],
            }
        )
        states_during_install: list[HostState] = []
        original_install = DeployOrchestrator._install_on

        def tracking_install(self, host):
            original_install(self, host)
            states_during_install.append(host.deployment_state)

        orch = DeployOrchestrator(cluster)
        monkeypatch.setattr(
            DeployOrchestrator, "_run_parallel",
            lambda self, fn, items: [fn(i) for i in items],
        )
        monkeypatch.setattr(DeployOrchestrator, "_install_on", tracking_install)
        _make_dummy_ssh_patcher(monkeypatch)

        orch._backend.install = lambda ssh: None
        orch._backend.init_controller = lambda ssh, **kw: ("ct", "wt")
        orch._backend.enable_worker_scheduling = lambda ssh: None
        orch._features.deploy = lambda ssh, feat: None

        orch.deploy()

        assert HostState.DEPLOYING in states_during_install


def _patched_install_on(self, host):
    """Stand-in for _install_on that marks DEPLOYING without SSH."""
    from zcc.models.state import HostState
    host.deployment_state = HostState.DEPLOYING
