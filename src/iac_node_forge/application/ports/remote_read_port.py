"""The boundary to the reads on a server (ADR-0009)."""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from iac_node_forge.domain.host_key import HostKeyCheck
from iac_node_forge.domain.manifest import Manifest
from iac_node_forge.domain.server_access import RemoteAccess
from iac_node_forge.domain.transfer_report import OverwriteEntry


@runtime_checkable
class RemoteReadPort(Protocol):
    """The reads on a server: host key, size, manifest and existing items.

    No method writes on the server, so a backup that uses only this port and
    ``FileTransferPort.pull`` is provably read-only. Every method raises a
    TransferError subclass (ConnectionFailedError, PermissionDeniedError,
    HostKeyChangedError, HostKeyUnknownError, RemotePathNotFoundError or
    TransferFailedError); the text never carries a secret.
    """

    def check_host_key(self, access: RemoteAccess) -> HostKeyCheck:
        """Read the host key the server shows and compare it with known_hosts.

        Returns KNOWN, UNKNOWN or CHANGED with the key shown. Nothing is
        written. Raises ConnectionFailedError when no key can be read.
        """
        ...

    def total_size(self, access: RemoteAccess, paths: Sequence[str]) -> int:
        """Return the disk usage in bytes of the absolute ``paths`` together.

        Raises RemotePathNotFoundError when a path does not exist.
        """
        ...

    def manifest(
        self, access: RemoteAccess, root: str, relative_paths: Sequence[str]
    ) -> Manifest:
        """Return the manifest of ``root``/``relative_path`` for each path.

        Entry paths are relative to the absolute ``root``, so
        ``("etc/nginx",)`` under ``/`` lists ``etc/nginx/nginx.conf`` as such.
        Raises RemotePathNotFoundError when a path does not exist.
        """
        ...

    def existing(
        self, access: RemoteAccess, root: str, names: Sequence[str]
    ) -> list[OverwriteEntry]:
        """Return the items among ``root``/``name`` that exist, with size and time.

        A name that does not exist is left out, not an error.
        """
        ...
