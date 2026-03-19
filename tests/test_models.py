"""Tests for Pydantic models: Host, Feature, Cluster."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zcc.models.cluster import Cluster
from zcc.models.feature import Feature
from zcc.models.host import (
    Host,
    HostRole,
    LimitConfig,
    PortConfig,
    ResourceRole,
    SSHConfig,
    StorageConfig,
    StoragePermission,
)


# ---------------------------------------------------------------------------
# Host
# ---------------------------------------------------------------------------


class TestHost:
    def _minimal(self, **overrides) -> dict:
        data = {
            "name": "node-1",
            "uri": "10.0.0.1",
            "roles": ["controller"],
        }
        data.update(overrides)
        return data

    def test_valid_controller(self):
        host = Host.model_validate(self._minimal())
        assert host.name == "node-1"
        assert host.roles == [HostRole.CONTROLLER]
        assert host.labels == []

    def test_valid_worker_with_labels(self):
        host = Host.model_validate(
            self._minimal(roles=["worker"], labels=["compute", "gpu"])
        )
        assert HostRole.WORKER in host.roles
        assert "compute" in host.labels

    def test_valid_sole_node(self):
        host = Host.model_validate(self._minimal(roles=["sole"]))
        assert HostRole.SOLE in host.roles

    def test_multiple_roles(self):
        host = Host.model_validate(
            self._minimal(roles=["controller", "worker"])
        )
        assert HostRole.CONTROLLER in host.roles
        assert HostRole.WORKER in host.roles

    def test_missing_roles_fails(self):
        with pytest.raises(ValidationError):
            Host.model_validate({"name": "n", "uri": "10.0.0.1"})

    def test_empty_roles_fails(self):
        with pytest.raises(ValidationError):
            Host.model_validate(self._minimal(roles=[]))

    def test_invalid_role_fails(self):
        with pytest.raises(ValidationError):
            Host.model_validate(self._minimal(roles=["master"]))

    def test_ssh_defaults(self):
        host = Host.model_validate(self._minimal())
        assert host.ssh.user == "root"
        assert host.ssh.port == 22
        assert host.ssh.key is None

    def test_ssh_custom(self):
        host = Host.model_validate(
            self._minimal(ssh={"user": "ubuntu", "port": 2222, "key": "/home/me/.ssh/id_rsa"})
        )
        assert host.ssh.user == "ubuntu"
        assert host.ssh.port == 2222

    def test_limit_percent_out_of_range(self):
        with pytest.raises(ValidationError):
            Host.model_validate(
                self._minimal(limits=[{"role": "cpu", "percent": 1.5}])
            )

    def test_limit_percent_valid_boundaries(self):
        host = Host.model_validate(
            self._minimal(
                limits=[
                    {"role": "cpu", "percent": 0.0},
                    {"role": "ram", "percent": 1.0},
                ]
            )
        )
        assert host.limits[0].percent == 0.0
        assert host.limits[1].percent == 1.0

    def test_storage_with_permissions(self):
        host = Host.model_validate(
            self._minimal(
                storage=[{"role": "data", "location": "/mnt/data", "permissions": "rw"}]
            )
        )
        assert host.storage[0].permissions == StoragePermission.RW

    def test_port_config(self):
        host = Host.model_validate(
            self._minimal(
                ports=[{"role": "api", "number": 8080, "direction": "in", "type": "tcp"}]
            )
        )
        assert host.ports[0].number == 8080


# ---------------------------------------------------------------------------
# Feature
# ---------------------------------------------------------------------------


class TestFeature:
    def test_valid_with_labels(self):
        feat = Feature.model_validate(
            {"name": "monitoring", "labels": ["compute"]}
        )
        assert feat.name == "monitoring"
        assert feat.labels == ["compute"]

    def test_valid_with_roles(self):
        feat = Feature.model_validate(
            {"name": "core", "roles": ["controller"]}
        )
        assert feat.roles == ["controller"]

    def test_valid_with_both(self):
        feat = Feature.model_validate(
            {"name": "full", "labels": ["primary"], "roles": ["worker"]}
        )
        assert feat.labels == ["primary"]
        assert feat.roles == ["worker"]

    def test_no_selector_fails(self):
        """A feature with neither labels nor roles must be rejected."""
        with pytest.raises(ValidationError):
            Feature.model_validate({"name": "orphan"})

    def test_install_cmds_alias(self):
        feat = Feature.model_validate(
            {
                "name": "app",
                "roles": ["worker"],
                "install-cmds": ["helm install app ./chart"],
                "init-cmds": ["kubectl apply -f ./init.yaml"],
            }
        )
        assert feat.install_cmds == ["helm install app ./chart"]
        assert feat.init_cmds == ["kubectl apply -f ./init.yaml"]

    def test_locations(self):
        feat = Feature.model_validate(
            {"name": "app", "labels": ["x"], "locations": ["./charts/app"]}
        )
        assert feat.locations == ["./charts/app"]


# ---------------------------------------------------------------------------
# Cluster
# ---------------------------------------------------------------------------


class TestCluster:
    def _minimal(self) -> dict:
        return {
            "name": "test-cluster",
            "hosts": [
                {"name": "ctrl", "uri": "10.0.0.1", "roles": ["controller"]}
            ],
        }

    def test_valid_minimal(self):
        cluster = Cluster.model_validate(self._minimal())
        assert cluster.name == "test-cluster"
        assert cluster.version == "v1"
        assert len(cluster.hosts) == 1
        assert cluster.features == []

    def test_no_hosts_fails(self):
        with pytest.raises(ValidationError):
            Cluster.model_validate({"name": "empty", "hosts": []})

    def test_no_controller_fails(self):
        with pytest.raises(ValidationError):
            Cluster.model_validate(
                {
                    "name": "bad",
                    "hosts": [
                        {"name": "w1", "uri": "10.0.0.2", "roles": ["worker"]}
                    ],
                }
            )

    def test_sole_node_satisfies_controller_requirement(self):
        cluster = Cluster.model_validate(
            {
                "name": "sole",
                "hosts": [
                    {"name": "s1", "uri": "10.0.0.1", "roles": ["sole"]}
                ],
            }
        )
        assert cluster.name == "sole"

    def test_duplicate_uris_fail(self):
        with pytest.raises(ValidationError):
            Cluster.model_validate(
                {
                    "name": "dup",
                    "hosts": [
                        {"name": "a", "uri": "10.0.0.1", "roles": ["controller"]},
                        {"name": "b", "uri": "10.0.0.1", "roles": ["worker"]},
                    ],
                }
            )

    def test_duplicate_feature_names_fail(self):
        with pytest.raises(ValidationError):
            Cluster.model_validate(
                {
                    "name": "dup",
                    "hosts": [
                        {"name": "c", "uri": "10.0.0.1", "roles": ["controller"]}
                    ],
                    "features": [
                        {"name": "mon", "labels": ["x"]},
                        {"name": "mon", "labels": ["y"]},
                    ],
                }
            )

    def test_version_default(self):
        cluster = Cluster.model_validate(self._minimal())
        assert cluster.version == "v1"

    def test_version_custom(self):
        data = self._minimal()
        data["version"] = "v2"
        cluster = Cluster.model_validate(data)
        assert cluster.version == "v2"

    def test_config_paths(self):
        data = self._minimal()
        data["config"] = ["./config/settings.yaml"]
        cluster = Cluster.model_validate(data)
        assert cluster.config == ["./config/settings.yaml"]

    def test_full_cluster(self):
        cluster = Cluster.model_validate(
            {
                "name": "full",
                "hosts": [
                    {
                        "name": "ctrl",
                        "uri": "10.0.0.1",
                        "roles": ["controller"],
                        "labels": ["primary"],
                    },
                    {
                        "name": "wrk",
                        "uri": "10.0.0.2",
                        "roles": ["worker"],
                        "labels": ["compute"],
                    },
                ],
                "features": [
                    {"name": "monitoring", "labels": ["primary", "compute"]},
                ],
            }
        )
        assert len(cluster.hosts) == 2
        assert len(cluster.features) == 1
