"""RemoteReadPort over the system's OpenSSH client (ADR-0009).

Only the read commands of the allow-list run on the server: ``du -sk``,
``find`` (whose ``-exec`` runs only ``sha256sum``, ``stat`` or ``readlink``)
and ``stat -c``.
"""

import re
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

from iac_node_forge.application.errors import TransferFailedError
from iac_node_forge.domain.host_key import HostKeyCheck
from iac_node_forge.domain.manifest import Manifest
from iac_node_forge.domain.remote_path import join_remote, validate_remote_path
from iac_node_forge.domain.server_access import RemoteAccess
from iac_node_forge.domain.transfer_report import OverwriteEntry
from iac_node_forge.infrastructure.openssh import _manifest_parse as parse
from iac_node_forge.infrastructure.openssh import _process
from iac_node_forge.infrastructure.openssh._host_keys import HostKeyInspector
from iac_node_forge.infrastructure.openssh.commands import OpenSshCommands

_KIBIBYTE = 1024
_BATCH = 100
_USAGE_LINE = re.compile(r"(\d+)\s.*", re.DOTALL)
_STAT_LINE = re.compile(r"(\d+) (\d+) (.+)", re.DOTALL)


class OpenSshRemoteReader:
    """Reads host keys, sizes, manifests and existing items over ssh."""

    def __init__(self, commands: OpenSshCommands | None = None) -> None:
        self._commands = commands or OpenSshCommands()
        self._inspector = HostKeyInspector(self._commands)

    def check_host_key(self, access: RemoteAccess) -> HostKeyCheck:
        """Return KNOWN, UNKNOWN or CHANGED for the key the server shows."""
        return self._inspector.check(access.host, access.port)

    def total_size(self, access: RemoteAccess, paths: Sequence[str]) -> int:
        """Return the bytes ``du -sk`` counts for ``paths`` together."""
        output = self._read(access, self._commands.disk_usage(paths), "du")
        usages = [_USAGE_LINE.fullmatch(line) for line in _lines(output)]
        if not usages or None in usages:
            raise TransferFailedError("the disk usage of the server could not be read")
        return sum(int(m.group(1)) for m in usages if m is not None) * _KIBIBYTE

    def manifest(
        self, access: RemoteAccess, root: str, relative_paths: Sequence[str]
    ) -> Manifest:
        """Return the manifest of the files and links under the paths."""
        validate_remote_path("root", root)
        paths = [join_remote(root, relative) for relative in relative_paths]
        commands = self._commands
        hashes = self._read(access, commands.find_hashes(paths), "find")
        sizes = self._read(access, commands.find_sizes(paths), "find")
        links = self._read(access, commands.find_links(paths), "find")
        return parse.build_manifest(
            parse.parse_hashes(hashes, root),
            parse.parse_sizes(sizes, root),
            parse.parse_links(links, root),
        )

    def existing(
        self, access: RemoteAccess, root: str, names: Sequence[str]
    ) -> list[OverwriteEntry]:
        """Return the items among ``root``/``name`` that exist."""
        paths = [join_remote(root, name) for name in names]
        found: list[OverwriteEntry] = []
        for batch in _batches(paths):
            command = self._commands.stat_paths(batch)
            completed = _process.run(self._commands.ssh(access, command))
            if completed.returncode != 0 and not _only_missing(completed.stderr):
                raise _process.failure(completed, "stat")
            found += [
                _overwrite_entry(line)
                for line in _lines(_process.text(completed.stdout))
            ]
        return found

    def _read(self, access: RemoteAccess, command: str, what: str) -> str:
        completed = _process.run(self._commands.ssh(access, command))
        return _process.text(_process.require_success(completed, what).stdout)


def _lines(output: str) -> list[str]:
    return [line for line in output.split("\n") if line]


def _only_missing(stderr: str) -> bool:
    lines = [line for line in stderr.splitlines() if line.strip()]
    return bool(lines) and all("no such file or directory" in x.lower() for x in lines)


def _overwrite_entry(line: str) -> OverwriteEntry:
    match = _STAT_LINE.fullmatch(line)
    if match is None:
        raise TransferFailedError("the stat output of the server could not be read")
    return OverwriteEntry(
        match.group(3),
        int(match.group(1)),
        datetime.fromtimestamp(int(match.group(2)), UTC),
    )


def _batches(paths: Sequence[str]) -> Iterator[Sequence[str]]:
    for start in range(0, len(paths), _BATCH):
        yield paths[start : start + _BATCH]
