"""Integration test — k0s backend against real containers.

This test is skipped automatically when the required environment variables are
absent, so it does not interfere with the regular unit-test suite.

Required environment variables
-------------------------------
ZCC_CTRL_IP     Container IP of the k0s controller node
ZCC_WORKER1_IP  Container IP of the first worker node
ZCC_WORKER2_IP  Container IP of the second worker node
ZCC_SSH_KEY     Absolute path to the private SSH key file

Optional environment variables
-------------------------------
ZCC_SSH_USER    SSH user inside the containers (default: ci)
"""

from __future__ import annotations

import os
import time

import pytest

from zcc.deploy.k0s import K0sInstaller
from zcc.deploy.orchestrator import DeployOrchestrator
from zcc.deploy.ssh import SSHClient
from zcc.models.cluster import Cluster

pytestmark = pytest.mark.integration

_REQUIRED = ("ZCC_CTRL_IP", "ZCC_WORKER1_IP", "ZCC_WORKER2_IP", "ZCC_SSH_KEY")

# Maximum seconds to wait for all worker nodes to appear as Ready
_NODE_READY_TIMEOUT = 180


def _cluster() -> Cluster:
    missing = [k for k in _REQUIRED if not os.getenv(k)]
    if missing:
        pytest.skip(f"Missing environment variables: {', '.join(missing)}")

    ctrl_ip = os.environ["ZCC_CTRL_IP"]
    worker1_ip = os.environ["ZCC_WORKER1_IP"]
    worker2_ip = os.environ["ZCC_WORKER2_IP"]
    ssh_key = os.environ["ZCC_SSH_KEY"]
    ssh_user = os.environ.get("ZCC_SSH_USER", "ci")

    return Cluster.model_validate(
        {
            "name": "ci-k0s",
            "hosts": [
                {
                    "name": "controller",
                    "uri": ctrl_ip,
                    "labels": ["controller"],
                    "ssh": {"user": ssh_user, "key": ssh_key},
                },
                {
                    "name": "worker-1",
                    "uri": worker1_ip,
                    "labels": ["worker"],
                    "ssh": {"user": ssh_user, "key": ssh_key},
                },
                {
                    "name": "worker-2",
                    "uri": worker2_ip,
                    "labels": ["worker"],
                    "ssh": {"user": ssh_user, "key": ssh_key},
                },
            ],
        }
    )


def test_k0s_three_node_cluster():
    """
    Deploy a 3-node k0s cluster (1 controller + 2 workers) using K0sInstaller
    against real privileged containers running systemd.

    Verification: ``k0s kubectl get nodes`` on the controller must eventually
    report all 3 nodes as Ready.
    """
    cluster = _cluster()

    orch = DeployOrchestrator(cluster, backend=K0sInstaller())
    orch.deploy()

    # k0s may need additional time to report worker nodes as Ready after join.
    ctrl_host = cluster.hosts[0]
    deadline = time.monotonic() + _NODE_READY_TIMEOUT
    ready_nodes: list[str] = []

    while time.monotonic() < deadline:
        with SSHClient(ctrl_host) as ssh:
            code, out, _ = ssh.run(
                "sudo k0s kubectl get nodes --no-headers "
                "--output custom-columns=NAME:.metadata.name,STATUS:.status.conditions[-1].type"
            )
        if code == 0:
            ready_nodes = [
                line.split()[0]
                for line in out.strip().splitlines()
                if line.strip() and line.strip().endswith("Ready")
            ]
            if len(ready_nodes) == 3:
                break
        time.sleep(10)

    assert len(ready_nodes) == 3, (
        f"Expected 3 Ready nodes, got {len(ready_nodes)}: {ready_nodes}"
    )
