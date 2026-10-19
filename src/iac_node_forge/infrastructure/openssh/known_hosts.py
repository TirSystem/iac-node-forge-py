"""KnownHostsPort over the system's OpenSSH tools (ADR-0009).

The one write the product makes outside the destination and the report: one
line appended to the user's known_hosts, on the explicit acceptance of a
fingerprint. The file is never rewritten and no line is removed.
"""

import os
from pathlib import Path

from iac_node_forge.application.errors import (
    HostKeyChangedError,
    HostKeyUnknownError,
)
from iac_node_forge.domain.host_key import HostKey
from iac_node_forge.infrastructure.openssh._host_keys import (
    HostKeyInspector,
    default_known_hosts,
)
from iac_node_forge.infrastructure.openssh.commands import OpenSshCommands, host_spec

_OWNER_ONLY_DIRECTORY = 0o700


class OpenSshKnownHosts:
    """Appends an accepted host key to known_hosts."""

    def __init__(self, commands: OpenSshCommands | None = None) -> None:
        self._commands = commands or OpenSshCommands()
        self._inspector = HostKeyInspector(self._commands)

    def trust(self, host_key: HostKey) -> None:
        """Append the key with this fingerprint, read from the server again."""
        if self._inspector.known_fingerprints(host_key.host, host_key.port):
            raise HostKeyChangedError(
                "the host already has an entry in known_hosts; fix it by hand"
            )
        scanned = self._inspector.scan(host_key.host, host_key.port)
        for key in scanned:
            if key.fingerprint == host_key.fingerprint:
                spec = host_spec(host_key.host, host_key.port)
                self._append(f"{spec} {key.key_type} {key.key}")
                return
        raise HostKeyUnknownError("the server now shows another host key")

    def _append(self, line: str) -> None:
        path = self._commands.known_hosts_file or default_known_hosts()
        path.parent.mkdir(parents=True, exist_ok=True, mode=_OWNER_ONLY_DIRECTORY)
        lead = "\n" if _lacks_final_newline(path) else ""
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{lead}{line}\n")
        if os.name == "posix":
            path.chmod(0o600)


def _lacks_final_newline(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("rb") as handle:
        handle.seek(-1, os.SEEK_END)
        return handle.read(1) != b"\n"
