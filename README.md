# zcc — Zero Cost Cluster

A Kubernetes cluster management framework powered by [k0s](https://k0sproject.io/).

`zcc` lets you describe a cluster in a single YAML file and deploy it with one
command.  Every node carries **labels**.  Two reserved labels map directly to
k0s controller behavior; all other labels are user-defined and used for feature
targeting.  **Features** — programs, configurations, storage capabilities —
are deployed to every node that shares at least one of their labels.

---

## Concepts

| Term | Description |
|------|-------------|
| **cluster** | A named set of hosts and the features running on them |
| **host** | A physical or virtual machine that joins the cluster |
| **label** | A string tag on a host.  Drives both k0s setup and feature targeting |
| **feature** | A capability deployed to every host that carries a matching label |

### Reserved labels

Two labels are reserved by the framework:

| Label | k0s equivalent | Description |
|-------|----------------|-------------|
| `controller` | k0s controller | Runs the Kubernetes control plane |
| `sole` | k0s controller | Runs control plane and also accepts workloads by removing controller NoSchedule taint |

Hosts are allowed to have no reserved labels. Any host that is not labeled
`controller` or `sole` is treated as a worker.
All other labels are user-defined and can be combined freely:

```yaml
labels: [controller, primary, monitoring]
labels: [compute, gpu, high-mem]
labels: [sole, dev]
```

---

## Quick start

```bash
pip install zcc
```

### Node prerequisites

Before running `zcc deploy`, every target node must satisfy two conditions:

1. **SSH key access** — `zcc` connects over SSH using the key or password you
   configure in the cluster YAML.  Password-based auth is supported but key-based
   auth is strongly recommended.

2. **Passwordless sudo** — both the k0s and Docker Swarm backends issue
   privileged commands using `sudo`.  The SSH user must be able to run `sudo`
   without a password prompt.  Create a `/etc/sudoers.d/` drop-in that
   permits only the exact commands `zcc` issues:

   **Docker Swarm backend**

   ```sudoers
   # /etc/sudoers.d/zcc-docker-swarm
   # Docker Engine upstream installer (curl … | sudo sh)
   ubuntu ALL=(ALL) NOPASSWD: /bin/sh
   # Post-install: add the SSH user to the docker group
   ubuntu ALL=(ALL) NOPASSWD: /usr/sbin/usermod
   ```

   After `install` completes and the next SSH connection is opened, the user is
   a member of the `docker` group, so no further `sudo` is needed for Docker
   Swarm commands (`swarm init`, `swarm join`, `docker info`, …).

   **k0s backend**

   ```sudoers
   # /etc/sudoers.d/zcc-k0s
   # k0s upstream installer (curl … | sudo sh)
   ubuntu ALL=(ALL) NOPASSWD: /bin/sh
   # All k0s lifecycle operations (install, start, token create, kubectl, status)
   ubuntu ALL=(ALL) NOPASSWD: /usr/local/bin/k0s
   ```

   k0s requires root for every lifecycle operation (service install, start,
   token generation, taint removal, status).  Running k0s as a non-root user is
   an [open upstream feature request](https://github.com/k0sproject/k0s/issues/5910)
   with no supported workaround at this time.

   Replace `ubuntu` with the SSH user configured in your cluster YAML in both
   files.

### 1. Write a cluster config

```yaml
# my-cluster.yaml
name: my-cluster
version: v1

hosts:
  - name: controller-1
    uri: 192.168.1.10
    labels: [controller, primary]
    ssh:
      user: ubuntu
      key: ~/.ssh/id_rsa

  - name: worker-1
    uri: 192.168.1.11
    labels: [compute]
    ssh:
      user: ubuntu
      key: ~/.ssh/id_rsa

features:
  - name: monitoring
    labels: [primary, compute]
    locations:
      - ./charts/monitoring
    install-cmds:
      - helm upgrade --install prometheus . --namespace monitoring --create-namespace
```

### 2. Validate the config

```bash
zcc validate my-cluster.yaml
```

### 3. Preview the deployment

```bash
zcc plan my-cluster.yaml
```

### 4. Deploy

```bash
zcc deploy my-cluster.yaml
```

---

## Configuration reference

See the `.schema` files at the root of this repository for the full field
reference.  They use a simple, self-describing meta-format in YAML:

| Schema file | Describes |
|-------------|-----------|
| `cluster.schema` | Top-level cluster document |
| `host.schema` | Individual node configuration |
| `feature.schema` | Capability deployed to label-selected nodes |

### Minimal cluster (sole node)

```yaml
name: dev-cluster
version: v1

hosts:
  - name: sole-node
    uri: 10.0.0.1
    labels: [sole]
    ssh:
      user: root
      key: ~/.ssh/id_rsa
```

### Full examples

See `examples/simple-cluster.yaml` and `examples/sole-node-cluster.yaml`.

---

## CLI reference

```
zcc --help

Commands:
  validate  Validate a cluster configuration file.
  plan      Show the deployment plan without executing it.
  deploy    Deploy a cluster from a configuration file.
```

| Option | Command | Description |
|--------|---------|-------------|
| `--dry-run` | `deploy` | Print the plan without making any changes |
| `-v / --verbose` | all | Enable debug-level logging |

---

## Architecture

```
zcc/
├── models/          Pydantic models — Cluster, Host, Feature
├── loader.py        YAML → validated Cluster
└── deploy/
    ├── ssh.py       SSH wrapper around paramiko with known_hosts verification
    ├── k0s.py       k0s binary install, controller join/bootstrap, worker join
    ├── feature.py   Feature file upload + command execution
    └── orchestrator.py  End-to-end deployment sequencing
```

Deployment order:

1. Install k0s binary on every node
2. Bootstrap the first `controller`/`sole` node → obtain controller + worker join-tokens
3. Join remaining controller nodes with the controller token
4. Join all non-controller nodes with the worker token
5. Deploy each feature to the hosts that carry its labels

---

## Development

```bash
pip install -e ".[dev]"
pytest
```
