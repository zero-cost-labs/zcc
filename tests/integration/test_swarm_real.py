"""Integration test — Docker Swarm backend against real containers.

This test is skipped automatically when the required environment variables are
absent, so it does not interfere with the regular unit-test suite.

Required environment variables
-------------------------------
ZCC_MANAGER_IP  Container IP of the Swarm manager node
ZCC_WORKER1_IP  Container IP of the first worker node
ZCC_WORKER2_IP  Container IP of the second worker node
ZCC_SSH_KEY     Absolute path to the private SSH key file

Optional environment variables
-------------------------------
ZCC_SSH_USER    SSH user inside the containers (default: ci)
"""

from __future__ import annotations

import os

import pytest

from zcc.deploy.orchestrator import DeployOrchestrator
from zcc.deploy.ssh import SSHClient
from zcc.deploy.swarm import DockerSwarmBackend
from zcc.models.cluster import Cluster

pytestmark = pytest.mark.integration

_REQUIRED = ("ZCC_MANAGER_IP", "ZCC_WORKER1_IP", "ZCC_WORKER2_IP", "ZCC_SSH_KEY")


def _cluster() -> Cluster:
    missing = [k for k in _REQUIRED if not os.getenv(k)]
    if missing:
        pytest.skip(f"Missing environment variables: {', '.join(missing)}")

    manager_ip = os.environ["ZCC_MANAGER_IP"]
    worker1_ip = os.environ["ZCC_WORKER1_IP"]
    worker2_ip = os.environ["ZCC_WORKER2_IP"]
    ssh_key = os.environ["ZCC_SSH_KEY"]
    ssh_user = os.environ.get("ZCC_SSH_USER", "ci")

    return Cluster.model_validate(
        {
            "name": "ci-swarm",
            "hosts": [
                {
                    "name": "manager",
                    "uri": manager_ip,
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


def test_swarm_three_node_cluster():
    """
    Deploy a 3-node Docker Swarm cluster (1 manager + 2 workers) using the
    DockerSwarmBackend against real Docker-in-Docker containers.

    Verification: ``docker node ls`` on the manager must list exactly 3 nodes.
    """
    cluster = _cluster()

    orch = DeployOrchestrator(cluster, backend=DockerSwarmBackend())
    orch.deploy()

    manager_host = cluster.hosts[0]
    with SSHClient(manager_host) as ssh:
        output = ssh.run_checked("docker node ls --format '{{.Hostname}}'")

    nodes = [line for line in output.strip().splitlines() if line.strip()]
    assert len(nodes) == 3, (
        f"Expected 3 nodes in the swarm, got {len(nodes)}.\n"
        f"docker node ls output:\n{output}"
    )
