"""The two worlds the transfer contract suite runs in (ADR-0001, ADR-0009).

``FakeWorld`` is the in-process fake server. ``SshdWorld`` is the real OpenSSH
adapter against the throw-away sshd container. Both give the suite the same
ports and the same few helpers to arrange the server, so one set of behaviours
is checked against both.
"""

import io
import subprocess
import tarfile
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from iac_node_forge.application.errors import ConnectionFailedError
from iac_node_forge.application.ports import (
    FileTransferPort,
    KnownHostsPort,
    LocalFilesystemPort,
    RemoteReadPort,
)
from iac_node_forge.domain.host_key import HostKeyStanding
from iac_node_forge.domain.server_access import RemoteAccess
from iac_node_forge.infrastructure.filesystem import LocalDiskFilesystem
from iac_node_forge.infrastructure.openssh import (
    OpenSshFileTransfer,
    OpenSshKnownHosts,
    OpenSshRemoteReader,
)
from tests.integration import sshd_server
from tests.integration.sshd_server import SshdServer
from tests.openssh_sandbox import isolated_commands
from tests.unit.application.transfer_fakes import FakeLocalFilesystem, FakeRemoteHost

UNREACHABLE_PORT = 2299


@dataclass(frozen=True, slots=True)
class Link:
    """A symbolic link in a tree to seed."""

    target: str


@dataclass(frozen=True, slots=True)
class NamedPipe:
    """A named pipe in a tree to seed; a transfer skips it."""


type Entry = bytes | Link | NamedPipe


class TransferWorld(ABC):
    """The ports under test and the helpers that arrange the server."""

    name: str
    access: RemoteAccess
    reader: RemoteReadPort
    transfer: FileTransferPort
    known_hosts: KnownHostsPort
    local: LocalFilesystemPort
    remote_root: str
    can_deny_reading: bool = True

    @abstractmethod
    def seed(self, tree: Mapping[str, Entry]) -> None:
        """Make the files, links and pipes of ``tree`` under the remote root.

        Keys are paths relative to the remote root; the server is emptied first.
        """

    @abstractmethod
    def arrange_host_key(self, standing: HostKeyStanding) -> None:
        """Make the user's known_hosts say KNOWN, UNKNOWN or CHANGED."""

    @abstractmethod
    def server_writes(self) -> int | None:
        """Return the writes the server received, or None when not observable."""

    @abstractmethod
    def unreachable_access(self) -> RemoteAccess:
        """Return an access whose server cannot be reached."""

    @abstractmethod
    def deny_reading(self, relative_path: str) -> None:
        """Make a path under the remote root unreadable for the user."""

    def remote(self, relative: str) -> str:
        """Return the absolute path of ``relative`` under the remote root."""
        return f"{self.remote_root}/{relative}"

    def under_root(self, relative: str) -> str:
        """Return ``remote_root``/``relative`` without the leading slash."""
        return self.remote(relative).lstrip("/")


class FakeWorld(TransferWorld):
    """The in-process fake server."""

    name = "fake"

    def __init__(self) -> None:
        self.host = FakeRemoteHost()
        self.access = RemoteAccess(self.host.key.host, 22, "tester", None)
        self.reader = self.host
        self.transfer = self.host
        self.known_hosts = self.host
        self.local = FakeLocalFilesystem()
        self.remote_root = "/srv/work"

    def seed(self, tree: Mapping[str, Entry]) -> None:
        self.host.nodes.clear()
        self.host.directories.clear()
        self.host.directories.add(self.remote_root)
        for relative, entry in tree.items():
            path = self.remote(relative)
            if isinstance(entry, bytes):
                self.host.add_file(path, entry)
            elif isinstance(entry, Link):
                self.host.add_link(path, entry.target)
            else:
                self.host.add_special(path, "named pipe")
        self.host.calls.clear()

    def arrange_host_key(self, standing: HostKeyStanding) -> None:
        self.host.forget_key()
        if standing is HostKeyStanding.KNOWN:
            self.host.know_current_key()
        elif standing is HostKeyStanding.CHANGED:
            self.host.know_current_key()
            self.host.change_key()

    def server_writes(self) -> int | None:
        return len(self.host.writes_on_server)

    def unreachable_access(self) -> RemoteAccess:
        self.host.fail_with = ConnectionFailedError("connection refused")
        return self.access

    def deny_reading(self, relative_path: str) -> None:
        self.host.unreadable.add(self.remote(relative_path))


class SshdWorld(TransferWorld):
    """The real OpenSSH adapters against the throw-away sshd container."""

    name = "openssh"

    def __init__(self, server: SshdServer, directory: Path) -> None:
        self.server = server
        self.directory = directory
        self.commands = isolated_commands(directory)
        self.access = RemoteAccess(
            server.host, server.port, server.user, str(server.key)
        )
        self.reader = OpenSshRemoteReader(self.commands)
        self.transfer = OpenSshFileTransfer(self.commands, timeout=180)
        self.known_hosts = OpenSshKnownHosts(self.commands)
        self.local = LocalDiskFilesystem()
        self.remote_root = server.remote_root
        self.known_hosts_file = directory / "known_hosts"
        self._clear_server()
        self.arrange_host_key(HostKeyStanding.KNOWN)  # like a new FakeRemoteHost

    def seed(self, tree: Mapping[str, Entry]) -> None:
        self._clear_server()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as tar:
            for relative, entry in tree.items():
                tar.addfile(_member(relative, entry), _content(entry))
        sshd_server.exec_in_container(
            ["sh", "-c", f"cd {self.remote_root} && tar -xf -"], buffer.getvalue()
        )

    def arrange_host_key(self, standing: HostKeyStanding) -> None:
        self.known_hosts_file.unlink(missing_ok=True)
        if standing is HostKeyStanding.KNOWN:
            self.known_hosts_file.write_text(self._scan(), encoding="utf-8")
        elif standing is HostKeyStanding.CHANGED:
            other = sshd_server.make_key_pair(self.directory, "id_other")
            key = sshd_server.public_key_line(other).split()[:2]
            line = f"[{self.server.host}]:{self.server.port} {key[0]} {key[1]}\n"
            self.known_hosts_file.write_text(line, encoding="utf-8")

    def server_writes(self) -> int | None:
        return None

    def unreachable_access(self) -> RemoteAccess:
        return RemoteAccess(
            self.server.host, UNREACHABLE_PORT, self.server.user, str(self.server.key)
        )

    def deny_reading(self, relative_path: str) -> None:
        sshd_server.exec_in_container(
            ["chmod", "000", f"{self.remote_root}/{relative_path}"]
        )

    def _clear_server(self) -> None:
        # Restore the permissions first: a denied directory cannot be removed.
        sshd_server.exec_in_container(
            [
                "sh",
                "-c",
                f"mkdir -p {self.remote_root} && cd {self.remote_root} "
                "&& chmod -R u+rwx . && rm -rf ./* ./.[!.]*",
            ]
        )

    def _scan(self) -> str:
        scan = subprocess.run(
            [
                "ssh-keyscan",
                "-p",
                str(self.server.port),
                "-t",
                "ed25519,ecdsa,rsa",
                self.server.host,
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        return scan.stdout.decode("utf-8")


def _member(relative: str, entry: Entry) -> tarfile.TarInfo:
    info = tarfile.TarInfo(relative)
    info.mode = 0o644
    if isinstance(entry, Link):
        info.type, info.linkname = tarfile.SYMTYPE, entry.target
    elif isinstance(entry, NamedPipe):
        info.type = tarfile.FIFOTYPE
    else:
        info.size = len(entry)
    return info


def _content(entry: Entry) -> io.BytesIO | None:
    return io.BytesIO(entry) if isinstance(entry, bytes) else None
