"""The boundary to the two tar streams between the PC and a server."""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from iac_node_forge.domain.server_access import RemoteAccess
from iac_node_forge.domain.transfer_report import SkippedEntry


@runtime_checkable
class FileTransferPort(Protocol):
    """Moves data as a tar stream; verification is the caller's, by manifest.

    Both methods raise a TransferError subclass and never carry a secret in the
    text. Devices, sockets and pipes are skipped and returned; a symbolic link
    is copied as a link and never followed. A member the extraction filter
    refuses (a link pointing outside the destination) is skipped with the
    reason, so the caller leaves it out of the comparison.
    """

    def pull(
        self, access: RemoteAccess, remote_paths: Sequence[str], destination: Path
    ) -> list[SkippedEntry]:
        """Unpack the absolute ``remote_paths`` under ``destination``.

        ``/etc/nginx`` becomes ``<destination>/etc/nginx``. Only reads run on the
        server. Raises UnsafeArchiveError for a member that is not under a
        requested path, or whose name is absolute or climbs out with ``..``.
        """
        ...

    def push(
        self,
        access: RemoteAccess,
        local_paths: Sequence[Path],
        remote_destination: str,
    ) -> list[SkippedEntry]:
        """Write each local path under ``remote_destination`` by its final name.

        The destination directory is created when it does not exist. This is the
        only write on the server.
        """
        ...
