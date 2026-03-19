# zcc — Zero Cost Cluster

A Kubernetes cluster management framework powered by [k0s](https://k0sproject.io/).

`zcc` lets you describe a cluster in a single YAML file and deploy it with one
command.  Nodes carry **roles** (`controller`, `worker`, `sole`) and arbitrary
**labels**; **features** — programs, configurations, storage capabilities — are
deployed to the nodes that match their label or role selectors.

---

## Concepts

| Term | Description |
|------|-------------|
| **cluster** | A named set of hosts and the features running on them |
| **host** | A physical or virtual machine that joins the cluster |
| **role** | What a host does in the cluster (`controller`, `worker`, or `sole`) |
| **label** | Arbitrary tag on a host used for fine-grained feature targeting |
| **feature** | A capability (Helm chart, script, config) deployed to matching hosts |

### Node roles

| Role | k0s equivalent | Description |
|------|----------------|-------------|
| `controller` | k0s controller | Runs the Kubernetes control plane |
| `worker` | k0s worker | Runs workloads (Pods) |
| `sole` | k0s controller --single | Control plane **and** workloads on one node |

---

## Quick start

```bash
pip install zcc
```

### 1. Write a cluster config

```yaml
# my-cluster.yaml
name: my-cluster
version: v1

hosts:
  - name: controller-1
    uri: 192.168.1.10
    roles: [controller]
    labels: [primary]
    ssh:
      user: ubuntu
      key: ~/.ssh/id_rsa

  - name: worker-1
    uri: 192.168.1.11
    roles: [worker]
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
reference.  They use an intentionally simple, self-describing meta-format:

| Schema file | Describes |
|-------------|-----------|
| `cluster.schema` | Top-level cluster document |
| `host.schema` | Individual node configuration |
| `feature.schema` | Capability deployed to selected nodes |

### Minimal cluster (sole node)

```yaml
name: dev-cluster
version: v1

hosts:
  - name: sole-node
    uri: 10.0.0.1
    roles: [sole]
    ssh:
      user: root
      key: ~/.ssh/id_rsa
```

### Full example

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
    ├── ssh.py       SSH client wrapper (paramiko)
    ├── k0s.py       k0s binary install, controller bootstrap, worker join
    ├── feature.py   Feature file upload + command execution
    └── orchestrator.py  End-to-end deployment sequencing
```

Deployment order:

1. Install k0s binary on every node
2. Bootstrap the first controller/sole node → obtain worker join-token
3. Bootstrap remaining controller nodes
4. Join all worker nodes with the token
5. Deploy each feature to its target hosts (matched by label or role)

---

## Development

```bash
pip install -e ".[dev]"
pytest
```

