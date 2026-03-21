"""SSH client wrapper around paramiko for host interactions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import paramiko

if TYPE_CHECKING:
    from ..models.host import Host

logger = logging.getLogger(__name__)


class SSHError(Exception):
    """Raised when an SSH operation fails."""


class SSHClient:
    """
    Thin wrapper around :class:`paramiko.SSHClient` that opens a connection
    to a cluster :class:`~zcc.models.host.Host`.

    Usage::

        with SSHClient(host) as ssh:
            code, out, err = ssh.run("uname -r")
    """

    def __init__(self, host: "Host") -> None:
        self.host = host
        self._client: paramiko.SSHClient | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> "SSHClient":
        cfg = self.host.ssh
        logger.debug("Connecting to %s@%s:%d", cfg.user, self.host.uri, cfg.port)

        client = paramiko.SSHClient()

        # Load the system and user known_hosts file(s) for host key
        # verification.  A known_hosts_file of "" disables the load; in that
        # case RejectPolicy still prevents connecting to an unverified host
        # unless the caller explicitly adds the key beforehand.
        if cfg.known_hosts_file is None:
            client.load_system_host_keys()
            client.load_host_keys(str(Path.home() / ".ssh" / "known_hosts"))
        elif cfg.known_hosts_file:
            client.load_host_keys(cfg.known_hosts_file)

        client.set_missing_host_key_policy(paramiko.RejectPolicy())

        kwargs: dict = {
            "hostname": self.host.uri,
            "port": cfg.port,
            "username": cfg.user,
        }
        if cfg.key:
            kwargs["key_filename"] = cfg.key
        elif cfg.password:
            kwargs["password"] = cfg.password

        try:
            client.connect(**kwargs)
        except paramiko.SSHException as exc:
            raise SSHError(
                f"Failed to connect to {self.host.uri}: {exc}"
            ) from exc

        self._client = client
        return self

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> "SSHClient":
        return self.connect()

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def run(self, command: str) -> tuple[int, str, str]:
        """
        Execute *command* on the remote host.

        Returns
        -------
        tuple[int, str, str]
            ``(exit_code, stdout, stderr)``
        """
        if self._client is None:
            raise SSHError("Not connected — call connect() or use as context manager")

        logger.debug("[%s] $ %s", self.host.uri, command)
        _, stdout, stderr = self._client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        logger.debug("[%s] exit=%d", self.host.uri, exit_code)
        return exit_code, out, err

    def run_checked(self, command: str) -> str:
        """
        Like :meth:`run`, but raises :exc:`SSHError` if the command exits
        with a non-zero status code.

        Returns
        -------
        str
            Combined stdout output.
        """
        code, out, err = self.run(command)
        if code != 0:
            raise SSHError(
                f"Command failed on {self.host.uri} (exit {code})\n"
                f"  cmd : {command}\n"
                f"  err : {err.strip()}"
            )
        return out

    def write_text(
        self, remote_path: str, content: str, *, mode: int | None = None
    ) -> None:
        """Write text content to *remote_path* on the remote host."""
        if self._client is None:
            raise SSHError("Not connected")
        sftp = self._client.open_sftp()
        try:
            with sftp.file(remote_path, "w") as remote_file:
                remote_file.write(content)
            if mode is not None:
                sftp.chmod(remote_path, mode)
        finally:
            sftp.close()

    # ------------------------------------------------------------------
    # File transfer
    # ------------------------------------------------------------------

    def upload(self, local_path: str, remote_path: str) -> None:
        """Upload a local file to the remote host via SFTP."""
        if self._client is None:
            raise SSHError("Not connected")
        sftp = self._client.open_sftp()
        try:
            sftp.put(local_path, remote_path)
        finally:
            sftp.close()
