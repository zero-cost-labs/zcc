"""Tests for deployment stack helpers: SSH wrapper, k0s installer, and deploy flow."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, call

from zcc.deploy.backend import ClusterBackend
from zcc.deploy.k0s import K0sInstaller
from zcc.deploy.orchestrator import DeployOrchestrator
from zcc.deploy.ssh import SSHClient
from zcc.deploy.swarm import DockerSwarmBackend
from zcc.models.cluster import Cluster


def _cluster(raw: dict) -> Cluster:
    return Cluster.model_validate(raw)


class TestSSHClient:
    def test_write_text_uses_sftp_file(self):
        host = _cluster(
            {
                "name": "t",
                "hosts": [{"name": "h", "uri": "10.0.0.1", "labels": ["controller"]}],
            }
        ).hosts[0]
        ssh = SSHClient(host)
        client = MagicMock()
        sftp = MagicMock()
        client.open_sftp.return_value = sftp
        ssh._client = client

        ssh.write_text("/tmp/token", "abc")

        sftp.file.assert_called_once_with("/tmp/token", "w")
        sftp.file.return_value.__enter__.return_value.write.assert_called_once_with("abc")
        sftp.close.assert_called_once()

    def test_write_text_applies_mode_when_provided(self):
        host = _cluster(
            {
                "name": "t",
                "hosts": [{"name": "h", "uri": "10.0.0.1", "labels": ["controller"]}],
            }
        ).hosts[0]
        ssh = SSHClient(host)
        client = MagicMock()
        sftp = MagicMock()
        client.open_sftp.return_value = sftp
        ssh._client = client

        ssh.write_text("/tmp/token", "abc", mode=0o600)

        sftp.chmod.assert_called_once_with("/tmp/token", 0o600)


class TestK0sInstaller:
    # Helper: returns (1,"","") for test -f (no config), (0,"","") for everything else.
    @staticmethod
    def _run_no_config(cmd: str) -> tuple[int, str, str]:
        return (1, "", "") if cmd.startswith("test -f") else (0, "", "")

    def test_init_controller_returns_both_tokens(self):
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.1"
        ssh.run_checked.side_effect = [
            "",
            "",
            "controller-token\n",
            "worker-token\n",
        ]
        ssh.run.side_effect = self._run_no_config

        ctl, wrk = K0sInstaller().init_controller(ssh)

        assert ctl == "controller-token"
        assert wrk == "worker-token"
        ssh.run_checked.assert_has_calls(
            [
                call("sudo k0s install controller"),
                call("sudo k0s start"),
                call("sudo k0s token create --role=controller"),
                call("sudo k0s token create --role=worker"),
            ]
        )

    def test_init_controller_enable_workers_removes_local_taint(self):
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.1"
        ssh.run_checked.side_effect = [
            "",
            "",
            "",
            "controller-token\n",
            "worker-token\n",
        ]
        ssh.run.side_effect = self._run_no_config

        K0sInstaller().init_controller(ssh, enable_workers=True)

        assert call(
            "sudo k0s kubectl taint nodes --all "
            "node-role.kubernetes.io/control-plane:NoSchedule- || true"
        ) in ssh.run_checked.call_args_list

    def test_join_controller_uses_controller_token_file(self):
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.2"
        ssh.run.return_value = (1, "", "")  # test -f → config file not present

        K0sInstaller().join_controller(ssh, "controller-token")

        ssh.write_text.assert_called_once_with(
            "/tmp/k0s-controller-token", "controller-token", mode=0o600
        )
        ssh.run_checked.assert_has_calls(
            [
                call("sudo k0s install controller --token-file /tmp/k0s-controller-token"),
                call("sudo k0s start"),
            ]
        )

    def test_join_controller_uses_config_when_present(self):
        """--config flag is appended when /etc/k0s/k0s.yaml exists on the remote."""
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.2"
        ssh.run.return_value = (0, "", "")  # test -f → config file present

        K0sInstaller().join_controller(ssh, "controller-token")

        ssh.run_checked.assert_has_calls(
            [
                call(
                    "sudo k0s install controller"
                    " --token-file /tmp/k0s-controller-token"
                    " --config /etc/k0s/k0s.yaml"
                ),
                call("sudo k0s start"),
            ]
        )

    def test_init_controller_uses_config_when_present(self):
        """--config flag is appended when /etc/k0s/k0s.yaml exists on the remote."""
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.1"
        ssh.run_checked.side_effect = [
            "",
            "",
            "controller-token\n",
            "worker-token\n",
        ]
        # test -f → config present (0); kubectl get nodes → success (0)
        ssh.run.return_value = (0, "", "")

        K0sInstaller().init_controller(ssh)

        ssh.run_checked.assert_has_calls(
            [
                call("sudo k0s install controller --config /etc/k0s/k0s.yaml"),
                call("sudo k0s start"),
            ]
        )

    def test_wait_for_ready_succeeds_on_first_poll(self):
        """_wait_for_ready returns immediately when kubectl get nodes succeeds."""
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.1"
        ssh.run.return_value = (0, "", "")

        K0sInstaller()._wait_for_ready(ssh)

        ssh.run.assert_called_once_with("sudo k0s kubectl get nodes")

    def test_wait_for_ready_retries_then_succeeds(self):
        """_wait_for_ready retries on failure and returns once kubectl succeeds."""
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.1"
        ssh.run.side_effect = [
            (1, "", "connection refused"),
            (1, "", "connection refused"),
            (0, "", ""),
        ]

        K0sInstaller()._wait_for_ready(ssh)

        assert ssh.run.call_count == 3

    def test_wait_for_ready_raises_after_timeout(self):
        """_wait_for_ready raises K0sError when the timeout expires."""
        from zcc.deploy.k0s import K0sError

        ssh = MagicMock()
        ssh.host.uri = "10.0.0.1"
        # Always returns failure so the timeout is guaranteed to expire.
        ssh.run.return_value = (1, "", "not ready")

        with pytest.raises(K0sError, match="did not become ready"):
            K0sInstaller()._wait_for_ready(ssh, timeout=1)


class TestDeployOrchestratorControllerJoin:
    def test_remaining_controllers_join_with_controller_token(self, monkeypatch):
        cluster = _cluster(
            {
                "name": "deploy",
                "hosts": [
                    {"name": "c1", "uri": "10.0.0.1", "labels": ["controller"]},
                    {"name": "c2", "uri": "10.0.0.2", "labels": ["controller"]},
                ],
            }
        )
        orch = DeployOrchestrator(cluster)

        monkeypatch.setattr(DeployOrchestrator, "_run_parallel", lambda self, fn, items: [fn(i) for i in items])
        monkeypatch.setattr(DeployOrchestrator, "_install_on", lambda self, host: None)

        class DummySSH:
            def __init__(self, host):
                self.host = host

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

        monkeypatch.setattr("zcc.deploy.orchestrator.SSHClient", DummySSH)

        calls: list[tuple] = []

        def init_controller(ssh, *, enable_workers=False):
            calls.append(("init", ssh.host.name, enable_workers))
            return ("controller-token", "worker-token")

        def join_controller(ssh, token):
            calls.append(("join-controller", ssh.host.name, token))

        def join_worker(ssh, token):
            calls.append(("join-worker", ssh.host.name, token))

        def deploy_feature(ssh, feature):
            calls.append(("feature", ssh.host.name, feature.name))

        orch._backend.init_controller = init_controller
        orch._backend.join_controller = join_controller
        orch._backend.join_worker = join_worker
        orch._backend.enable_worker_scheduling = lambda ssh: calls.append(
            ("untaint", ssh.host.name)
        )
        orch._features.deploy = deploy_feature

        orch.deploy()

        assert ("init", "c1", True) in calls
        assert ("join-controller", "c2", "controller-token") in calls
        assert ("untaint", "c2") in calls

    def test_no_untaint_when_remaining_controller_not_sole(self, monkeypatch):
        cluster = _cluster(
            {
                "name": "deploy",
                "hosts": [
                    {"name": "c1", "uri": "10.0.0.1", "labels": ["controller"]},
                    {"name": "c2", "uri": "10.0.0.2", "labels": ["controller"]},
                    {"name": "w1", "uri": "10.0.0.3", "labels": ["gpu"]},
                ],
            }
        )
        orch = DeployOrchestrator(cluster)

        monkeypatch.setattr(
            DeployOrchestrator, "_run_parallel", lambda self, fn, items: [fn(i) for i in items]
        )
        monkeypatch.setattr(DeployOrchestrator, "_install_on", lambda self, host: None)

        class DummySSH:
            def __init__(self, host):
                self.host = host

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

        monkeypatch.setattr("zcc.deploy.orchestrator.SSHClient", DummySSH)

        calls: list[tuple] = []

        def init_controller(ssh, *, enable_workers=False):
            calls.append(("init", ssh.host.name, enable_workers))
            return ("controller-token", "worker-token")

        def join_controller(ssh, token):
            calls.append(("join-controller", ssh.host.name, token))

        def join_worker(ssh, token):
            calls.append(("join-worker", ssh.host.name, token))

        orch._backend.init_controller = init_controller
        orch._backend.join_controller = join_controller
        orch._backend.join_worker = join_worker
        orch._backend.enable_worker_scheduling = lambda ssh: calls.append(
            ("untaint", ssh.host.name)
        )
        orch._features.deploy = lambda ssh, feature: None

        orch.deploy()

        assert ("join-controller", "c2", "controller-token") in calls
        assert ("join-worker", "w1", "worker-token") in calls
        assert ("untaint", "c2") not in calls


class TestClusterBackend:
    """Tests for the ClusterBackend abstract interface and custom backend injection."""

    def test_k0s_installer_is_a_cluster_backend(self):
        """K0sInstaller must satisfy the ClusterBackend interface."""
        assert issubclass(K0sInstaller, ClusterBackend)
        assert isinstance(K0sInstaller(), ClusterBackend)

    def test_cannot_instantiate_cluster_backend_directly(self):
        """ClusterBackend is abstract and cannot be instantiated directly."""
        import pytest

        with pytest.raises(TypeError):
            ClusterBackend()  # type: ignore[abstract]

    def test_orchestrator_defaults_to_k0s_backend(self):
        """When no backend is supplied, the orchestrator uses K0sInstaller."""
        cluster = _cluster(
            {
                "name": "t",
                "hosts": [{"name": "h", "uri": "10.0.0.1", "labels": ["controller"]}],
            }
        )
        orch = DeployOrchestrator(cluster)
        assert isinstance(orch._backend, K0sInstaller)

    def test_orchestrator_accepts_custom_backend(self):
        """A custom ClusterBackend implementation is accepted by the orchestrator."""

        class DummyBackend(ClusterBackend):
            def install(self, ssh):
                pass

            def init_controller(self, ssh, *, enable_workers=False):
                return ("ct", "wt")

            def enable_worker_scheduling(self, ssh):
                pass

            def join_controller(self, ssh, token):
                pass

            def join_worker(self, ssh, token):
                pass

            def status(self, ssh):
                return "ok"

        cluster = _cluster(
            {
                "name": "t",
                "hosts": [{"name": "h", "uri": "10.0.0.1", "labels": ["controller"]}],
            }
        )
        backend = DummyBackend()
        orch = DeployOrchestrator(cluster, backend=backend)
        assert orch._backend is backend

    def test_orchestrator_uses_injected_backend_during_deploy(self, monkeypatch):
        """The orchestrator calls the injected backend, not a hardcoded K0sInstaller."""

        class TrackingBackend(ClusterBackend):
            def __init__(self):
                self.calls: list[str] = []

            def install(self, ssh):
                self.calls.append("install")

            def init_controller(self, ssh, *, enable_workers=False):
                self.calls.append("init_controller")
                return ("ct", "wt")

            def enable_worker_scheduling(self, ssh):
                self.calls.append("enable_worker_scheduling")

            def join_controller(self, ssh, token):
                self.calls.append("join_controller")

            def join_worker(self, ssh, token):
                self.calls.append("join_worker")

            def status(self, ssh):
                return "ok"

        cluster = _cluster(
            {
                "name": "t",
                "hosts": [{"name": "h", "uri": "10.0.0.1", "labels": ["controller"]}],
            }
        )
        backend = TrackingBackend()
        orch = DeployOrchestrator(cluster, backend=backend)

        monkeypatch.setattr(
            DeployOrchestrator, "_run_parallel", lambda self, fn, items: [fn(i) for i in items]
        )

        class DummySSH:
            def __init__(self, host):
                self.host = host

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

        monkeypatch.setattr("zcc.deploy.orchestrator.SSHClient", DummySSH)
        orch._features.deploy = lambda ssh, feature: None

        orch.deploy()

        assert "install" in backend.calls
        assert "init_controller" in backend.calls


class TestDockerSwarmBackend:
    def test_is_a_cluster_backend(self):
        assert issubclass(DockerSwarmBackend, ClusterBackend)
        assert isinstance(DockerSwarmBackend(), ClusterBackend)

    def test_install_runs_docker_install_script(self):
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.1"
        ssh.host.ssh.user = "ubuntu"

        DockerSwarmBackend().install(ssh)

        ssh.run_checked.assert_has_calls(
            [
                call("curl -fsSL https://get.docker.com | sudo sh"),
                call("sudo usermod -aG docker ubuntu"),
            ]
        )
        assert ssh.run_checked.call_count == 2

    def test_init_controller_returns_both_tokens(self):
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.1"
        ssh.run_checked.side_effect = [
            "",                   # docker swarm init
            "SWMTKN-manager\n",   # join-token manager -q
            "SWMTKN-worker\n",    # join-token worker -q
        ]

        mgr, wrk = DockerSwarmBackend().init_controller(ssh)

        assert mgr == "SWMTKN-manager"
        assert wrk == "SWMTKN-worker"
        ssh.run_checked.assert_has_calls(
            [
                call("docker swarm init --advertise-addr 10.0.0.1"),
                call("docker swarm join-token manager -q"),
                call("docker swarm join-token worker -q"),
            ]
        )

    def test_init_controller_records_manager_addr(self):
        ssh = MagicMock()
        ssh.host.uri = "192.168.1.10"
        ssh.run_checked.side_effect = ["", "mgr-tok\n", "wrk-tok\n"]

        backend = DockerSwarmBackend()
        backend.init_controller(ssh)

        assert backend._manager_addr == "192.168.1.10:2377"

    def test_init_controller_enable_workers_is_noop(self):
        """enable_workers has no effect for Docker Swarm (managers always run workloads)."""
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.1"
        ssh.run_checked.side_effect = ["", "mgr-tok\n", "wrk-tok\n"]

        # Should not raise and call count should be identical with or without flag
        DockerSwarmBackend().init_controller(ssh, enable_workers=True)

        assert ssh.run_checked.call_count == 3

    def test_join_controller_uses_manager_token_and_addr(self):
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.2"

        backend = DockerSwarmBackend()
        backend._manager_addr = "10.0.0.1:2377"
        backend.join_controller(ssh, "SWMTKN-manager")

        ssh.run_checked.assert_called_once_with(
            "docker swarm join --token SWMTKN-manager 10.0.0.1:2377"
        )

    def test_join_worker_uses_worker_token_and_addr(self):
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.3"

        backend = DockerSwarmBackend()
        backend._manager_addr = "10.0.0.1:2377"
        backend.join_worker(ssh, "SWMTKN-worker")

        ssh.run_checked.assert_called_once_with(
            "docker swarm join --token SWMTKN-worker 10.0.0.1:2377"
        )

    def test_enable_worker_scheduling_is_noop(self):
        """enable_worker_scheduling must not issue any SSH command."""
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.1"

        DockerSwarmBackend().enable_worker_scheduling(ssh)

        ssh.run_checked.assert_not_called()
        ssh.run.assert_not_called()

    def test_status_returns_swarm_state(self):
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.1"
        ssh.run.return_value = (0, "active\n", "")

        result = DockerSwarmBackend().status(ssh)

        assert result == "active"
        ssh.run.assert_called_once_with(
            "docker info --format '{{.Swarm.LocalNodeState}}'"
        )

    def test_status_returns_error_message_when_docker_not_running(self):
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.1"
        ssh.run.return_value = (1, "", "Cannot connect to the Docker daemon")

        result = DockerSwarmBackend().status(ssh)

        assert "docker not running or not installed" in result

    def test_join_controller_raises_if_init_not_called(self):
        """join_controller raises DockerSwarmError when manager addr is unset."""
        from zcc.deploy.swarm import DockerSwarmError

        ssh = MagicMock()
        ssh.host.uri = "10.0.0.2"

        with pytest.raises(DockerSwarmError, match="call init_controller first"):
            DockerSwarmBackend().join_controller(ssh, "some-token")

    def test_join_worker_raises_if_init_not_called(self):
        """join_worker raises DockerSwarmError when manager addr is unset."""
        from zcc.deploy.swarm import DockerSwarmError

        ssh = MagicMock()
        ssh.host.uri = "10.0.0.3"

        with pytest.raises(DockerSwarmError, match="call init_controller first"):
            DockerSwarmBackend().join_worker(ssh, "some-token")

    def test_manager_addr_propagates_to_subsequent_joins(self):
        """After init_controller the same addr is used for both join calls."""
        init_ssh = MagicMock()
        init_ssh.host.uri = "10.0.0.1"
        init_ssh.run_checked.side_effect = ["", "mgr-tok\n", "wrk-tok\n"]

        join_ssh = MagicMock()
        join_ssh.host.uri = "10.0.0.2"

        backend = DockerSwarmBackend()
        mgr_tok, wrk_tok = backend.init_controller(init_ssh)
        backend.join_worker(join_ssh, wrk_tok)

        join_ssh.run_checked.assert_called_once_with(
            f"docker swarm join --token {wrk_tok} 10.0.0.1:2377"
        )

