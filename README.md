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

Before running `zcc deploy`, every target node must satisfy the following
conditions.

#### SSH access

`zcc` connects to every node over SSH using the key (or password) you configure
in the cluster YAML.  Key-based authentication is strongly recommended.

#### Passwordless sudo

Both the k0s and Docker Swarm backends issue privileged commands using `sudo`.
The SSH user must be able to run those commands without a password prompt.
Create a `/etc/sudoers.d/` drop-in that restricts access to only the exact
commands `zcc` uses:

**Docker Swarm backend**

```sudoers
# /etc/sudoers.d/zcc-docker-swarm
# Docker Engine upstream installer (curl … | sudo sh)
ubuntu ALL=(ALL) NOPASSWD: /bin/sh
# Post-install: add the SSH user to the docker group
ubuntu ALL=(ALL) NOPASSWD: /usr/sbin/usermod
```

After `install` completes and the next SSH connection is opened the user is a
member of the `docker` group, so no further `sudo` is needed for Docker Swarm
commands (`swarm init`, `swarm join`, `docker info`, …).

**k0s backend**

```sudoers
# /etc/sudoers.d/zcc-k0s
# k0s upstream installer (curl … | sudo sh)
ubuntu ALL=(ALL) NOPASSWD: /bin/sh
# All k0s lifecycle operations (install, start, token create, kubectl, status)
ubuntu ALL=(ALL) NOPASSWD: /usr/local/bin/k0s
# Config file upload (backend: section): create /etc/k0s directory and move temp files
ubuntu ALL=(ALL) NOPASSWD: /usr/bin/mkdir -p /etc/k0s
ubuntu ALL=(ALL) NOPASSWD: /usr/bin/mv /tmp/zcc-k0s.yaml.tmp /etc/k0s/k0s.yaml
```

k0s requires root for every lifecycle operation (service install, start,
token generation, taint removal, status).  Running k0s as a non-root user is
an [open upstream feature request](https://github.com/k0sproject/k0s/issues/5910)
with no supported workaround at this time.

Replace `ubuntu` with the SSH user configured in your cluster YAML in both
files.

#### Required OS packages (k0s backend)

The default k0s CNI plugin, **kube-router**, requires the following packages to
be present on every cluster node:

| Package | Purpose |
|---------|---------|
| `ipset` | kube-router uses ipset to manage service firewall rules |
| `conntrack` (`conntrack-tools`) | Connection tracking required by kube-proxy and kube-router |
| `iptables` | Network policy enforcement |
| `iproute2` | Route management (BGP routes added by kube-router) |

Install on Debian/Ubuntu nodes:

```bash
sudo apt-get install -y ipset conntrack iptables iproute2
```

Install on RHEL/CentOS/Fedora nodes:

```bash
sudo dnf install -y ipset conntrack-tools iptables iproute
```

kube-router also loads kernel modules at start-up (`ip_vs`, `xt_set`,
`ip_tables`, `nf_conntrack`).  These are built into every mainstream Linux
distribution kernel.  If your nodes use a stripped-down custom kernel, ensure
`/lib/modules/$(uname -r)/` is populated and `modprobe` is available.

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

## Network and firewall requirements (k0s backend)

### IP forwarding

k0s requires packet forwarding to be enabled on every node:

```bash
# Apply immediately
sudo sysctl -w net.ipv4.ip_forward=1

# Persist across reboots
echo "net.ipv4.ip_forward = 1" | sudo tee /etc/sysctl.d/99-k0s.conf
sudo sysctl --system
```

### Required ports

Open the following ports between nodes.  All entries are TCP unless noted.

| Port | Direction | Purpose |
|------|-----------|---------|
| 22 | controller ↔ workers | SSH (used by `zcc` itself) |
| 6443 | workers → controllers | Kubernetes API server |
| 8132 | workers → controllers | konnectivity agent tunnel |
| 9443 | controllers → controllers | k0s controller join API (multi-controller clusters) |
| 2380 | controllers → controllers | etcd peer communication (multi-controller clusters) |
| 179 (TCP) | all nodes ↔ all nodes | BGP (kube-router peer sessions) |
| 4 (proto, not port) | all nodes ↔ all nodes | IP-in-IP encapsulation (kube-router `overlay=full`, the default) |
| 10250 | controllers → workers | kubelet API (metrics, logs, exec) |

> **Note:** Port 4 is an IP *protocol number*, not a TCP/UDP port.  In iptables
> terms: `iptables -A FORWARD -p 4 -j ACCEPT`.  If your host's `FORWARD` chain
> defaults to `DROP` (common on hardened systems), add that rule explicitly.

### FORWARD chain

On systems where the iptables `FORWARD` chain policy is `DROP` (e.g. Debian
with `ufw` enabled, or any hardened baseline), traffic between k0s nodes and
pods will be silently dropped.  Allow forwarding across the cluster network
interface:

```bash
# Replace <iface> with the interface that carries cluster traffic (e.g. eth0, ens3)
sudo iptables -A FORWARD -i <iface> -j ACCEPT
sudo iptables -A FORWARD -o <iface> -j ACCEPT
# IP-in-IP (kube-router overlay, protocol 4)
sudo iptables -A FORWARD -p 4 -j ACCEPT

# Make persistent (Debian/Ubuntu)
sudo apt-get install -y iptables-persistent
sudo netfilter-persistent save
```

If you use `ufw`, the equivalent is:

```bash
# /etc/ufw/before.rules — add before the *filter block
-A ufw-before-forward -i <iface> -j ACCEPT
-A ufw-before-forward -o <iface> -j ACCEPT
```

---

## Backend configuration

The optional top-level `backend:` section lets you supply backend-specific
deployment settings directly in the cluster YAML, instead of pre-placing files
on nodes manually.

### `arguments`

Key/value pairs under `arguments` are forwarded as CLI flags to every
`k0s install controller` and `k0s install worker` invocation.  Values are
automatically shell-quoted.

```yaml
backend:
  arguments:
    --network: calico
```

Generates `sudo k0s install controller --network calico` (and the same for
worker joins).

### `config_files` — inline or path-based

Any key that is *not* `arguments` is treated as a **configuration file entry**.
The key is the logical filename; the value is either:

* **Inline content** — a YAML/TOML/any-text block scalar written directly in the
  cluster file.
* **A local filesystem path** — an absolute or relative path to an existing file
  on the machine running `zcc`.

During `install`, `zcc` reads the content (expanding a path if needed) and
uploads it to every node via SFTP.  The uploaded path is determined by the
backend.  For the k0s backend:

| Key | Remote destination |
|-----|--------------------|
| `k0s.yaml` | `/etc/k0s/k0s.yaml` |

`zcc` creates the parent directory with `sudo mkdir -p` and moves the file into
place with `sudo mv`, so no pre-existing directory or elevated SFTP session is
required.  These commands must be permitted in the node's sudoers drop-in —
they are included in the `zcc-k0s` example under [Node prerequisites](#node-prerequisites).

#### Inline k0s configuration

```yaml
backend:
  arguments:
    --network: calico
  k0s.yaml: |
    apiVersion: k0s.k0sproject.io/v1beta1
    kind: ClusterConfig
    metadata:
      name: my-cluster
    spec:
      network:
        provider: kuberouter
        kubeRouter:
          autoMTU: true
          extraArgs:
            overlay: "off"
```

#### k0s configuration from a local file

```yaml
backend:
  k0s.yaml: ./config/k0s.yaml
```

`./config/k0s.yaml` is read from the machine running `zcc` and uploaded to
`/etc/k0s/k0s.yaml` on every cluster node.

### Relationship to the pre-existing `/etc/k0s/k0s.yaml` check

The k0s backend also detects a pre-existing `/etc/k0s/k0s.yaml` on each node
(placed by Ansible, cloud-init, Packer, …) and passes `--config` automatically
if found.  When you supply `k0s.yaml` via `backend:`, the file is written during
`install` and the `--config` flag is picked up on the same node in the same
deployment run — no separate provisioning step needed.

**Overwrite semantics:** when `k0s.yaml` is provided under `backend:`, `zcc`
**completely replaces** any pre-existing `/etc/k0s/k0s.yaml` on every node.
The upload uses an atomic `sudo mv`, so the result is the exact content you
supplied — there is no merging with the pre-placed file.

YAML merging is intentionally not supported because it introduces several
intractable corner cases with the k0s config schema:

* **List fields** — k0s uses lists for chart extensions, worker profiles, and
  extra API-server arguments.  Append-semantics are ambiguous and make it
  impossible to *remove* an entry set in the pre-placed file.
* **Key removal** — standard YAML has no tombstone/null-override mechanism, so
  a merge can never delete a key that the pre-placed config already set.
* **Type conflicts** — if the same key is a scalar in one file and a map in the
  other, merge behaviour is undefined.
* **Idempotency** — merge output depends on the per-node pre-placed file at
  deployment time; nodes provisioned differently would produce different final
  configs, making repeated deployments unpredictable.

If you need to build on top of a pre-placed config, copy its contents into the
`backend: k0s.yaml` value and extend it there before running `zcc`.

---

## k0s tuning

### kube-router overlay mode

kube-router supports three overlay modes:

| Value | Behaviour | When to use |
|-------|-----------|-------------|
| `full` (default) | IPIP tunnels between all nodes | Nodes on different subnets or when you cannot guarantee direct L3 reachability |
| `subnet` | IPIP only across subnet boundaries; direct routing within a subnet | Mixed topology |
| `off` | Pure BGP direct routing, no encapsulation | All nodes share the same L2 segment (e.g. one cloud VPC subnet, one rack) |

Use `overlay: "off"` when all nodes are on the same L2 segment to eliminate
unnecessary encapsulation overhead and avoid the need to allow IP protocol 4
through firewalls.  Do **not** use `overlay: "off"` when nodes span subnets —
pods will lose connectivity.

```yaml
spec:
  network:
    provider: kuberouter
    kubeRouter:
      extraArgs:
        overlay: "off"
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

The top-level `backend:` section in a cluster YAML is validated by the
`BackendConfig` model.  See [Backend configuration](#backend-configuration) below.

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
├── models/          Pydantic models — Cluster, Host, Feature, BackendConfig
├── loader.py        YAML → validated Cluster
└── deploy/
    ├── ssh.py       SSH wrapper around paramiko with known_hosts verification
    ├── k0s.py       k0s binary install, controller join/bootstrap, worker join
    ├── swarm.py     Docker Swarm backend
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
