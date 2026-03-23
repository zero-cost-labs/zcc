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

# Maximum seconds to wait for all worker nodes to appear as Ready.
# kube-router (the default k0s CNI) must pull its image and configure
# networking before kubelet marks nodes as Ready — allow plenty of time.
_NODE_READY_TIMEOUT = 300


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
    report both worker nodes as Ready.  The controller is a pure control-plane
    node (Workloads: false) and does not register a kubelet, so only the two
    worker nodes appear in ``kubectl get nodes``.
    """
    cluster = _cluster()

    orch = DeployOrchestrator(cluster, backend=K0sInstaller())
    orch.deploy()

    # k0s may need additional time to report worker nodes as Ready after join.
    # kube-router (the default CNI) must pull its image and set up BGP/iptables
    # before kubelet transitions each node from NotReady → Ready.
    ctrl_host = cluster.hosts[0]
    deadline = time.monotonic() + _NODE_READY_TIMEOUT
    ready_nodes: list[str] = []

    while time.monotonic() < deadline:
        with SSHClient(ctrl_host) as ssh:
            code, out, _ = ssh.run(
                "sudo k0s kubectl get nodes --no-headers"
            )
        if code == 0 and out.strip():
            # Standard 'kubectl get nodes --no-headers' columns:
            #   NAME   STATUS   ROLES   AGE   VERSION
            # STATUS is "Ready" or "NotReady".  Avoid the conditions[-1].type
            # jsonpath approach: when a CNI plugin adds a NetworkUnavailable
            # condition it lands last in the array, so conditions[-1].type
            # returns "NetworkUnavailable" instead of "Ready".
            ready_nodes = [
                parts[0]
                for line in out.strip().splitlines()
                if (parts := line.split()) and len(parts) >= 2 and parts[1] == "Ready"
            ]
            if len(ready_nodes) == 2:
                break
        time.sleep(10)

    assert len(ready_nodes) == 2, (
        f"Expected 2 Ready nodes, got {len(ready_nodes)}: {ready_nodes}"
    )
