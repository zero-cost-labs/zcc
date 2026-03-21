"""Feature deployer — installs features on their targeted hosts."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .ssh import SSHClient, SSHError

if TYPE_CHECKING:
    from ..models.feature import Feature

logger = logging.getLogger(__name__)

_REMOTE_WORK_DIR = "/tmp/zcc-features"


class FeatureDeployer:
    """
    Deploys a :class:`~zcc.models.feature.Feature` onto a remote host.

    Deployment steps for each feature:

    1. Create a remote working directory.
    2. Upload every local path listed in ``feature.locations`` to that
       directory.
    3. Run all ``install-cmds`` sequentially inside the working directory.
    4. Run all ``init-cmds`` sequentially inside the working directory.
    """

    def deploy(
        self, ssh: SSHClient, feature: "Feature", work_dir: str = _REMOTE_WORK_DIR
    ) -> None:
        """
        Deploy *feature* onto the host behind *ssh*.

        Parameters
        ----------
        ssh:
            Open SSH connection to the target host.
        feature:
            The feature to deploy.
        work_dir:
            Remote directory used as the working space during deployment.
        """
        logger.info(
            "[%s] Deploying feature '%s'", ssh.host.uri, feature.name
        )

        # Ensure the remote working directory exists.
        ssh.run_checked(f"mkdir -p {work_dir}/{feature.name}")
        remote_dir = f"{work_dir}/{feature.name}"

        # Upload local file/directory trees.
        for location in feature.locations:
            self._upload_location(ssh, location, remote_dir)

        # Run installation commands.
        for cmd in feature.install_cmds:
            logger.debug(
                "[%s] feature '%s' install: %s", ssh.host.uri, feature.name, cmd
            )
            ssh.run_checked(f"cd {remote_dir} && {cmd}")

        # Run init commands.
        for cmd in feature.init_cmds:
            logger.debug(
                "[%s] feature '%s' init: %s", ssh.host.uri, feature.name, cmd
            )
            ssh.run_checked(f"cd {remote_dir} && {cmd}")

        logger.info(
            "[%s] Feature '%s' deployed successfully", ssh.host.uri, feature.name
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _upload_location(
        self, ssh: SSHClient, local_path: str, remote_dir: str
    ) -> None:
        """
        Upload a local path (file or directory) to *remote_dir*.

        Directories are walked recursively; individual files are uploaded
        directly via SFTP.
        """
        local = Path(local_path)
        if not local.exists():
            raise SSHError(
                f"Feature location '{local_path}' does not exist locally"
            )

        if local.is_file():
            remote_path = f"{remote_dir}/{local.name}"
            logger.debug("Uploading %s -> %s", local, remote_path)
            ssh.upload(str(local), remote_path)
        elif local.is_dir():
            for root, _dirs, files in os.walk(local):
                rel_root = Path(root).relative_to(local)
                remote_subdir = f"{remote_dir}/{local.name}/{rel_root}".rstrip("/.")
                ssh.run_checked(f"mkdir -p {remote_subdir}")
                for filename in files:
                    src = Path(root) / filename
                    dst = f"{remote_subdir}/{filename}"
                    logger.debug("Uploading %s -> %s", src, dst)
                    ssh.upload(str(src), dst)
