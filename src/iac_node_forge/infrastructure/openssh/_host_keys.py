"""Reading the host keys of a server and the entries of known_hosts (ADR-0009)."""

from dataclasses import dataclass
from pathlib import Path

from iac_node_forge.application.errors import ConnectionFailedError, TransferFailedError
from iac_node_forge.domain.host_key import HostKey, HostKeyCheck, HostKeyStanding
from iac_node_forge.infrastructure.openssh import _process
from iac_node_forge.infrastructure.openssh.commands import OpenSshCommands

_FIELDS_OF_A_KEY_LINE = 3
_NO_ENTRY = 1


@dataclass(frozen=True, slots=True)
class ScannedKey:
    """A key a server showed: its type, the key itself and its fingerprint."""

    key_type: str
    key: str
    fingerprint: str


class HostKeyInspector:
    """Compares the keys a server shows with the entries of known_hosts."""

    def __init__(self, commands: OpenSshCommands) -> None:
        self._commands = commands

    def scan(self, host: str, port: int) -> list[ScannedKey]:
        """Return the keys the server shows, the preferred type first.

        Raises ConnectionFailedError when it shows none.
        """
        completed = _process.run(self._commands.keyscan(host, port))
        lines = _key_lines(_process.text(completed.stdout))
        if not lines:
            raise ConnectionFailedError(
                f"no host key could be read from {host} port {port}"
            )
        keys = [
            ScannedKey(fields[1], fields[2], fingerprint)
            for fields, fingerprint in zip(
                (line.split() for line in lines),
                self._fingerprints(lines),
                strict=True,
            )
        ]
        return sorted(keys, key=_preference)

    def known_fingerprints(self, host: str, port: int) -> list[str]:
        """Return the fingerprints of the entries known_hosts holds for the host."""
        file = self._commands.known_hosts_file
        if file is not None and not file.exists():
            return []
        completed = _process.run(self._commands.known_entries(host, port))
        if completed.returncode == _NO_ENTRY:
            return []
        if completed.returncode != 0:
            raise TransferFailedError("known_hosts could not be read")
        lines = _key_lines(_process.text(completed.stdout))
        return self._fingerprints(lines) if lines else []

    def check(self, host: str, port: int) -> HostKeyCheck:
        """Return where the key the server shows stands against known_hosts."""
        scanned = self.scan(host, port)
        known = self.known_fingerprints(host, port)
        if not known:
            return HostKeyCheck(
                HostKeyStanding.UNKNOWN, _host_key(host, port, scanned[0])
            )
        for key in scanned:
            if key.fingerprint in known:
                return HostKeyCheck(HostKeyStanding.KNOWN, _host_key(host, port, key))
        return HostKeyCheck(HostKeyStanding.CHANGED, _host_key(host, port, scanned[0]))

    def _fingerprints(self, key_lines: list[str]) -> list[str]:
        payload = ("\n".join(key_lines) + "\n").encode("utf-8")
        completed = _process.run(self._commands.fingerprints(), stdin=payload)
        if completed.returncode != 0:
            raise TransferFailedError("a host key fingerprint could not be computed")
        fingerprints = [
            line.split()[1]
            for line in _process.text(completed.stdout).splitlines()
            if line.strip()
        ]
        if len(fingerprints) != len(key_lines):
            raise TransferFailedError("a host key fingerprint could not be computed")
        return fingerprints


def default_known_hosts() -> Path:
    """Return the user's own known_hosts file."""
    return Path.home() / ".ssh" / "known_hosts"


def _key_lines(output: str) -> list[str]:
    return [
        line.strip()
        for line in output.splitlines()
        if line.strip()
        and not line.startswith("#")
        and len(line.split()) >= _FIELDS_OF_A_KEY_LINE
    ]


def _preference(key: ScannedKey) -> int:
    for rank, marker in enumerate(("ed25519", "ecdsa")):
        if marker in key.key_type:
            return rank
    return 2


def _host_key(host: str, port: int, key: ScannedKey) -> HostKey:
    return HostKey(host, port, key.key_type, key.fingerprint)
