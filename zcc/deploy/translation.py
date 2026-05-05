"""Backend translator ABC — runtime bridge between heterogeneous backends.

This module defines :class:`BackendTranslator`, the **deploy-time**
counterpart to the data-model
:class:`~zcc.models.translation.TranslationRecipe`.  Where the recipe
declares *that* a bridge between two backend types exists, the translator
provides the *how* — the actual SSH commands and token-exchange logic
required to connect nodes running different cluster-backend technologies.

Current status
--------------
No concrete :class:`BackendTranslator` is implemented yet.  The ABC
establishes the seams where future federation logic must be provided.
Registering a translator with
:class:`~zcc.deploy.orchestrator.DeployOrchestrator` is the mechanism by
which that logic is wired into the deployment pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.backend import BackendType
    from .backend import ClusterBackend
    from .ssh import SSHClient


class BackendTranslator(ABC):
    """Abstract runtime interface for bridging two heterogeneous backends.

    A :class:`BackendTranslator` is the **deploy-time counterpart** to the
    data-model :class:`~zcc.models.translation.TranslationRecipe`.

    Registration
    ------------
    Concrete translators are registered with
    :class:`~zcc.deploy.orchestrator.DeployOrchestrator` via the
    ``translators`` constructor parameter.  The orchestrator looks up the
    translator for each ``(source_type, target_type)`` pair and invokes
    :meth:`translate_join_token` and :meth:`bridge_network` as needed
    during the deployment sequence.

    .. note:: Intrinsic limitations

        k0s (Kubernetes) and Docker Swarm are architecturally incompatible:

        * **Control-plane gap** — k0s uses etcd + the Kubernetes API
          server; Swarm uses Raft consensus inside the Docker daemon.
          Neither speaks the other's wire protocol.
        * **Join-protocol gap** — k0s join tokens are opaque to Docker
          Swarm and vice versa; a translator must issue separate,
          equivalent credentials on each side.
        * **Overlay-network gap** — k0s relies on CNI plugins (kube-router,
          Calico, …); Swarm maintains its own VXLAN overlay.  Pods and
          Swarm services cannot address each other directly without a
          cross-cluster network fabric (e.g. Submariner, Cilium Cluster
          Mesh, or a custom service mesh).

        A concrete :class:`BackendTranslator` therefore needs to deploy a
        federation layer *above* both platforms — an approach more akin to
        a meta-scheduler than a native cluster join.

    See Also
    --------
    :class:`~zcc.models.translation.TranslationRecipe` — the data-model
        counterpart declared in the cluster YAML.
    :class:`~zcc.deploy.orchestrator.DeployOrchestrator` — consumes
        registered translators during deployment.
    """

    @property
    @abstractmethod
    def source_type(self) -> "BackendType":
        """The backend type this translator reads from (the *source* side)."""

    @property
    @abstractmethod
    def target_type(self) -> "BackendType":
        """The backend type this translator writes to (the *target* side)."""

    @abstractmethod
    def translate_join_token(
        self,
        token: str,
        source_backend: "ClusterBackend",
        target_backend: "ClusterBackend",
    ) -> str:
        """Adapt a join token from *source_backend* for use by *target_backend*.

        In practice this may involve standing up a proxy or gateway service
        on one side, exchanging credentials out-of-band, or issuing an
        equivalent join token in the target system.

        Parameters
        ----------
        token :
            Raw join token produced by
            :meth:`~zcc.deploy.backend.ClusterBackend.init_controller` on
            the source backend.
        source_backend :
            The :class:`~zcc.deploy.backend.ClusterBackend` instance that
            produced *token*.
        target_backend :
            The :class:`~zcc.deploy.backend.ClusterBackend` instance that
            will consume the translated token.

        Returns
        -------
        str
            A join token (or equivalent credential) understood by
            *target_backend*.
        """

    @abstractmethod
    def bridge_network(
        self,
        source_ssh: "SSHClient",
        target_ssh: "SSHClient",
    ) -> None:
        """Establish a network path between a source- and a target-backend node.

        This must be called before any workload on *source_ssh*'s host
        attempts to communicate with a workload on *target_ssh*'s host.
        Implementations are responsible for configuring whatever overlay
        network, tunnel, or service-mesh integration is required to bridge
        the two backend networking domains.

        Parameters
        ----------
        source_ssh :
            Open SSH connection to a node running :attr:`source_type`.
        target_ssh :
            Open SSH connection to a node running :attr:`target_type`.
        """
