"""Tests for deployment stack helpers: SSH wrapper, k0s installer, and deploy flow."""

from __future__ import annotations

from unittest.mock import MagicMock, call

from zcc.deploy.k0s import K0sInstaller
from zcc.deploy.orchestrator import DeployOrchestrator
from zcc.deploy.ssh import SSHClient
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


class TestK0sInstaller:
    def test_init_controller_returns_both_tokens(self):
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.1"
        ssh.run_checked.side_effect = [
            "",
            "",
            "controller-token\n",
            "worker-token\n",
        ]

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

        K0sInstaller().init_controller(ssh, enable_workers=True)

        assert call(
            'sudo k0s kubectl taint nodes "$(hostname)" '
            "node-role.kubernetes.io/control-plane:NoSchedule-"
        ) in ssh.run_checked.call_args_list

    def test_join_controller_uses_controller_token_file(self):
        ssh = MagicMock()
        ssh.host.uri = "10.0.0.2"

        K0sInstaller().join_controller(ssh, "controller-token")

        ssh.write_text.assert_called_once_with("/tmp/k0s-controller-token", "controller-token")
        ssh.run_checked.assert_has_calls(
            [
                call("sudo k0s install controller --token-file /tmp/k0s-controller-token"),
                call("sudo k0s start"),
            ]
        )


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
        monkeypatch.setattr(DeployOrchestrator, "_install_k0s_on", lambda self, host: None)

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

        orch._k0s.init_controller = init_controller
        orch._k0s.join_controller = join_controller
        orch._k0s.join_worker = join_worker
        orch._k0s.enable_worker_scheduling = lambda ssh: calls.append(
            ("untaint", ssh.host.name)
        )
        orch._features.deploy = deploy_feature

        orch.deploy()

        assert ("init", "c1", True) in calls
        assert ("join-controller", "c2", "controller-token") in calls
