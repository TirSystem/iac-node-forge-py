"""In-process fakes of the transfer ports (ADR-0009, ADR-0001).

``FakeRemoteHost`` is one fake server that implements RemoteReadPort,
FileTransferPort and KnownHostsPort: a tree of files, links and special files
in a dict, a fake known_hosts, a scripted connection failure, a way to corrupt
one byte in transit and a recorder of every operation it received, so a test
can assert that nothing was written on the server. ``FakeLocalFilesystem`` is
the real local disk with a configurable amount of free room.
"""

import functools
import hashlib
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from iac_node_forge.application.errors import (
    HostKeyChangedError,
    HostKeyUnknownError,
    PermissionDeniedError,
    RemotePathNotFoundError,
    TransferError,
)
from iac_node_forge.domain.host_key import HostKey, HostKeyCheck, HostKeyStanding
from iac_node_forge.domain.manifest import EntryKind, Manifest, ManifestEntry
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.remote_path import (
    join_remote,
    validate_relative_path,
    validate_remote_path,
)
from iac_node_forge.domain.server_access import RemoteAccess, ServerAccess
from iac_node_forge.domain.transfer_report import OverwriteEntry, SkippedEntry
from iac_node_forge.infrastructure.filesystem import LocalDiskFilesystem
from iac_node_forge.presentation.cli.services import TransferServices

KIBIBYTE = 1024
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
SERVER_KEY = HostKey("192.0.2.10", 22, "ssh-ed25519", "SHA256:serverkeyone")
OTHER_KEY = HostKey("192.0.2.10", 22, "ssh-ed25519", "SHA256:serverkeytwo")

# Every operation the fake can receive, and whether it writes on the server.
# The real adapter's remote commands are the allow-list of ADR-0009; this table
# is the same list seen from the ports.
OPERATIONS: dict[str, bool] = {
    "check_host_key": False,
    "total_size": False,
    "manifest": False,
    "existing": False,
    "pull": False,
    "push": True,
    "trust": False,
}


@dataclass(frozen=True, slots=True)
class FakeFile:
    """A regular file of the fake server."""

    content: bytes
    modified_at: datetime = EPOCH


@dataclass(frozen=True, slots=True)
class FakeLink:
    """A symbolic link of the fake server."""

    target: str


@dataclass(frozen=True, slots=True)
class FakeSpecial:
    """A device, socket or pipe of the fake server."""

    reason: str


type FakeNode = FakeFile | FakeLink | FakeSpecial


@dataclass(frozen=True, slots=True)
class RemoteCall:
    """One operation the fake server received."""

    operation: str
    detail: str

    @property
    def writes_server(self) -> bool:
        """Return True when the operation writes on the server."""
        return OPERATIONS[self.operation]


@dataclass
class FakeRemoteHost:
    """A fake server, its known_hosts and the recorder of what it received."""

    key: HostKey = SERVER_KEY
    nodes: dict[str, FakeNode] = field(default_factory=dict)
    directories: set[str] = field(default_factory=set)
    known: dict[tuple[str, int], str] = field(default_factory=dict)
    fail_with: TransferError | None = None
    corrupt_in_transit: bool = False
    start_known: bool = True
    unreadable: set[str] = field(default_factory=set)
    calls: list[RemoteCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.start_known:
            self.know_current_key()

    # Setting up the tree and the scripted behaviour ---------------------------

    def add_file(self, path: str, content: bytes, modified: datetime = EPOCH) -> None:
        """Add a regular file at the absolute ``path``."""
        self._add(path, FakeFile(content, modified))

    def add_link(self, path: str, target: str) -> None:
        """Add a symbolic link at the absolute ``path``."""
        self._add(path, FakeLink(target))

    def add_special(self, path: str, reason: str = "device") -> None:
        """Add a device, socket or pipe at the absolute ``path``."""
        self._add(path, FakeSpecial(reason))

    def add_directory(self, path: str) -> None:
        """Add an (empty) directory at the absolute ``path``."""
        self.directories.add(validate_remote_path("path", path))

    def know_current_key(self) -> None:
        """Put the key the server shows now into the fake known_hosts.

        A new fake starts like this, so that ssh would accept the host.
        """
        self.known[(self.key.host, self.key.port)] = self.key.fingerprint

    def forget_key(self) -> None:
        """Remove the entry of the host from the fake known_hosts."""
        self.known.clear()

    def change_key(self) -> None:
        """Make the server show another key than the one it showed."""
        self.key = HostKey(
            self.key.host, self.key.port, self.key.key_type, OTHER_KEY.fingerprint
        )

    def content(self, path: str) -> bytes:
        """Return the content of the file at ``path``."""
        node = self.nodes[path]
        assert isinstance(node, FakeFile)
        return node.content

    @property
    def writes_on_server(self) -> list[RemoteCall]:
        """Return the received operations that write on the server."""
        return [call for call in self.calls if call.writes_server]

    # RemoteReadPort ------------------------------------------------------------

    def check_host_key(self, access: RemoteAccess) -> HostKeyCheck:
        """Return the standing of the key the server shows."""
        self._receive("check_host_key", access.host)
        recorded = self.known.get((access.host, access.port))
        if recorded is None:
            return HostKeyCheck(HostKeyStanding.UNKNOWN, self.key)
        standing = (
            HostKeyStanding.KNOWN
            if recorded == self.key.fingerprint
            else HostKeyStanding.CHANGED
        )
        return HostKeyCheck(standing, self.key)

    def total_size(self, access: RemoteAccess, paths: Sequence[str]) -> int:
        """Return the disk usage of the paths, each file rounded up to 1 KiB."""
        self._receive("total_size", " ".join(paths), access)
        total = 0
        for path in paths:
            self._require_exists(path)
            self._require_readable(path)
            for node in self._under(path).values():
                if isinstance(node, FakeFile):
                    total += -(-len(node.content) // KIBIBYTE) * KIBIBYTE
        return total

    def manifest(
        self, access: RemoteAccess, root: str, relative_paths: Sequence[str]
    ) -> Manifest:
        """Return the manifest of the files and links under the paths."""
        self._receive("manifest", " ".join(relative_paths), access)
        entries: list[ManifestEntry] = []
        for relative in relative_paths:
            path = join_remote(root, relative)
            self._require_exists(path)
            self._require_readable(path)
            for node_path, node in self._under(path).items():
                entry = _entry(node_path[len(root.rstrip("/")) + 1 :], node)
                if entry is not None:
                    entries.append(entry)
        return Manifest.of(entries)

    def existing(
        self, access: RemoteAccess, root: str, names: Sequence[str]
    ) -> list[OverwriteEntry]:
        """Return the items among the names that exist on the server."""
        self._receive("existing", " ".join(names), access)
        found: list[OverwriteEntry] = []
        for name in names:
            path = join_remote(root, name)
            node = self.nodes.get(path)
            if isinstance(node, FakeFile):
                found.append(OverwriteEntry(path, len(node.content), node.modified_at))
            elif isinstance(node, FakeLink):
                found.append(OverwriteEntry(path, len(node.target), EPOCH))
            elif self._is_directory(path):
                found.append(OverwriteEntry(path, 0, EPOCH))
        return found

    # FileTransferPort ------------------------------------------------------------

    def pull(
        self, access: RemoteAccess, remote_paths: Sequence[str], destination: Path
    ) -> list[SkippedEntry]:
        """Write the remote paths under ``destination`` at their full path."""
        self._receive("pull", " ".join(remote_paths), access)
        skipped: list[SkippedEntry] = []
        corrupt = self.corrupt_in_transit
        for path in remote_paths:
            self._require_exists(path)
            self._require_readable(path)
            for directory in sorted(self.directories):
                if directory == path or directory.startswith(path.rstrip("/") + "/"):
                    (destination / directory.lstrip("/")).mkdir(
                        parents=True, exist_ok=True
                    )
            for node_path, node in self._under(path).items():
                local = destination / node_path.lstrip("/")
                local.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(node, FakeSpecial):
                    skipped.append(SkippedEntry(node_path, node.reason))
                elif isinstance(node, FakeLink):
                    _write_link(local, node, node_path, skipped)
                else:
                    local.write_bytes(_flip(node.content) if corrupt else node.content)
                    corrupt = False
        return skipped

    def push(
        self,
        access: RemoteAccess,
        local_paths: Sequence[Path],
        remote_destination: str,
    ) -> list[SkippedEntry]:
        """Write the local paths under the destination by their final name."""
        self._receive("push", remote_destination, access)
        validate_remote_path("remote destination", remote_destination)
        self.directories.add(remote_destination)
        skipped: list[SkippedEntry] = []
        corrupt = [self.corrupt_in_transit]
        for top in local_paths:
            self._store(top, top.name, remote_destination, skipped, corrupt)
        return skipped

    # KnownHostsPort ----------------------------------------------------------------

    def trust(self, host_key: HostKey) -> None:
        """Record the key the server shows, when it is the one accepted."""
        self._receive("trust", host_key.host)
        if (host_key.host, host_key.port) in self.known:
            raise HostKeyChangedError("the host already has an entry")
        if host_key.fingerprint != self.key.fingerprint:
            raise HostKeyUnknownError("the server now shows another host key")
        self.known[(host_key.host, host_key.port)] = host_key.fingerprint

    # Internals ---------------------------------------------------------------------

    def _receive(
        self, operation: str, detail: str, access: RemoteAccess | None = None
    ) -> None:
        assert operation in OPERATIONS, "an operation outside the allow-list"
        self.calls.append(RemoteCall(operation, detail))
        if self.fail_with is not None:
            raise self.fail_with
        if access is not None:
            self._require_accepted_key(access)

    def _require_accepted_key(self, access: RemoteAccess) -> None:
        """Do what ssh does with StrictHostKeyChecking=yes."""
        recorded = self.known.get((access.host, access.port))
        if recorded is None:
            raise HostKeyUnknownError("the host key is not accepted in known_hosts")
        if recorded != self.key.fingerprint:
            raise HostKeyChangedError("the host key differs from known_hosts")

    def _require_readable(self, path: str) -> None:
        # find, tar and du all fail when anything at or below the path is closed.
        for blocked in self.unreadable:
            inside = path == blocked or path.startswith(blocked.rstrip("/") + "/")
            contains = blocked.startswith(path.rstrip("/") + "/")
            if inside or contains:
                raise PermissionDeniedError("a remote path may not be read")

    def _add(self, path: str, node: FakeNode) -> None:
        self.nodes[validate_remote_path("path", path)] = node

    def _is_directory(self, path: str) -> bool:
        prefix = path.rstrip("/") + "/"
        return (
            path in self.directories
            or any(other.startswith(prefix) for other in self.nodes)
            or any(other.startswith(prefix) for other in self.directories)
        )

    def _require_exists(self, path: str) -> None:
        if path not in self.nodes and not self._is_directory(path):
            raise RemotePathNotFoundError("a remote path does not exist")

    def _under(self, path: str) -> dict[str, FakeNode]:
        prefix = path.rstrip("/") + "/"
        return {
            other: node
            for other, node in sorted(self.nodes.items())
            if other == path or other.startswith(prefix)
        }

    def _store(
        self,
        local: Path,
        relative: str,
        destination: str,
        skipped: list[SkippedEntry],
        corrupt: list[bool],
    ) -> None:
        remote = join_remote(destination, validate_relative_path("name", relative))
        if local.is_symlink():
            self.nodes[remote] = FakeLink(os.readlink(local))
        elif local.is_dir():
            self.directories.add(remote)
            for child in sorted(local.iterdir()):
                self._store(
                    child, f"{relative}/{child.name}", destination, skipped, corrupt
                )
        elif local.is_file():
            data = local.read_bytes()
            if corrupt[0]:
                data, corrupt[0] = _flip(data), False
            self.nodes[remote] = FakeFile(data, EPOCH)
        else:
            skipped.append(SkippedEntry(str(local), "special file"))


class FakeLocalFilesystem(LocalDiskFilesystem):
    """The local disk with a configurable amount of free room."""

    def __init__(self, free: int | None = None) -> None:
        self.free = free

    def free_bytes(self, directory: Path) -> int:
        """Return the configured room, or the real one when none is set."""
        return self.free if self.free is not None else super().free_bytes(directory)


class FakeServerAccessStore:
    """ServerAccessStore in memory."""

    def __init__(self) -> None:
        self.saved: dict[tuple[ConfigurationId, str], ServerAccess] = {}

    def save(self, access: ServerAccess) -> None:
        """Keep the access, replacing the one of the same server."""
        self.saved[(access.configuration_id, access.server_identifier)] = access

    def load(
        self, configuration_id: ConfigurationId, server_identifier: str
    ) -> ServerAccess | None:
        """Return the access of the server, or None."""
        return self.saved.get((configuration_id, server_identifier))


@functools.cache
def symlinks_supported() -> bool:
    """Return True when this computer lets the tests create symbolic links."""
    with tempfile.TemporaryDirectory() as directory:
        try:
            os.symlink("target", Path(directory) / "link")
        except OSError:
            return False
    return True


def _entry(relative: str, node: FakeNode) -> ManifestEntry | None:
    if isinstance(node, FakeFile):
        digest = hashlib.sha256(node.content).hexdigest()
        return ManifestEntry(relative, EntryKind.FILE, len(node.content), digest, None)
    if isinstance(node, FakeLink):
        return ManifestEntry(relative, EntryKind.LINK, 0, None, node.target)
    return None


def _flip(data: bytes) -> bytes:
    if not data:
        return data
    return bytes([data[0] ^ 0x01]) + data[1:]


def _write_link(
    local: Path, node: FakeLink, remote_path: str, skipped: list[SkippedEntry]
) -> None:
    if node.target.startswith("/"):
        # What the extraction filter of the real adapter does.
        skipped.append(SkippedEntry(remote_path, "refused: absolute link target"))
        return
    os.symlink(node.target, local)


@dataclass
class FakeTransferBundle:
    """The fake server, local disk and access store a command-line test shares."""

    host: FakeRemoteHost = field(default_factory=FakeRemoteHost)
    filesystem: FakeLocalFilesystem = field(default_factory=FakeLocalFilesystem)
    accesses: FakeServerAccessStore = field(default_factory=FakeServerAccessStore)

    def services(self) -> TransferServices:
        """Return the CLI services that use these fakes."""
        return TransferServices(
            server_access=self.accesses,
            remote_reader=self.host,
            file_transfer=self.host,
            known_hosts=self.host,
            local_filesystem=self.filesystem,
        )
