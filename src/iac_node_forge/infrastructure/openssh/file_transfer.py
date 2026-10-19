"""FileTransferPort: a tar stream over the system's OpenSSH client (ADR-0009).

A backup reads ``tar -cf -`` from the stdout of ssh; a restore writes a tar
stream to the stdin of ``mkdir -p`` and ``tar -xf -``. Nothing else is run on
the server and no file name is placed on a shell command line unquoted.
"""

from collections.abc import Sequence
from pathlib import Path

from iac_node_forge.application.errors import (
    TransferError,
    TransferFailedError,
    UnsafeArchiveError,
)
from iac_node_forge.domain.remote_path import validate_remote_path
from iac_node_forge.domain.server_access import RemoteAccess
from iac_node_forge.domain.transfer_report import SkippedEntry
from iac_node_forge.infrastructure.openssh import _process, _tar_stream
from iac_node_forge.infrastructure.openssh._process import StreamedProcess
from iac_node_forge.infrastructure.openssh.commands import OpenSshCommands, tar_names

_EMPTY_ARCHIVE = bytes(10240)
"""Twenty zero blocks: a tar archive without members."""
_GRACE_SECONDS = 10.0
"""How long a command that lost its stream may take to end with its own status."""


class OpenSshFileTransfer:
    """Moves files between the PC and a server as a tar stream over ssh."""

    def __init__(
        self,
        commands: OpenSshCommands | None = None,
        *,
        timeout: float = _process.STREAM_TIMEOUT,
    ) -> None:
        self._commands = commands or OpenSshCommands()
        self._timeout = timeout

    def pull(
        self, access: RemoteAccess, remote_paths: Sequence[str], destination: Path
    ) -> list[SkippedEntry]:
        """Unpack the remote paths under ``destination`` at their full path."""
        names = tar_names(remote_paths)
        argv = self._commands.ssh(access, self._commands.tar_create(remote_paths))
        with StreamedProcess(argv, capture=True, timeout=self._timeout) as process:
            try:
                skipped = _tar_stream.unpack(process.stdout, destination, names)
            except UnsafeArchiveError:
                process.abort()
                raise
            except TransferFailedError as broken:
                raise self._explain(process, broken) from broken
            _process.require_success(process.finish(), "tar")
        return skipped

    def push(
        self,
        access: RemoteAccess,
        local_paths: Sequence[Path],
        remote_destination: str,
    ) -> list[SkippedEntry]:
        """Write the local paths under ``remote_destination`` by final name."""
        validate_remote_path("remote destination", remote_destination)
        command = self._commands.make_directory_and_extract(
            remote_destination, self._tar_knows_no_same_owner(access)
        )
        with StreamedProcess(
            self._commands.ssh(access, command), feed=True, timeout=self._timeout
        ) as process:
            try:
                skipped = _tar_stream.pack(process.stdin, local_paths)
            except OSError as broken:
                # The remote end closed the pipe: its status says why.
                raise self._explain(
                    process, TransferFailedError("the connection broke")
                ) from broken
            _process.require_success(process.finish(), "tar")
        return skipped

    def _explain(
        self, process: StreamedProcess, fallback: TransferError
    ) -> TransferError:
        process.wait_for_end(_GRACE_SECONDS)
        if process.is_running():
            process.abort()
            process.finish()
            return fallback
        completed = process.finish()
        if completed.returncode == 0:
            return fallback
        return _process.failure(completed, "tar")

    def _tar_knows_no_same_owner(self, access: RemoteAccess) -> bool:
        probe = self._commands.tar_owner_probe()
        completed = _process.run(
            self._commands.ssh(access, probe), stdin=_EMPTY_ARCHIVE
        )
        if completed.returncode == _process.SSH_FAILURE:
            raise _process.failure(completed, "tar")
        return completed.returncode == 0
