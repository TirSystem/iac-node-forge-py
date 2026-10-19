"""The host key of a server and where it stands against ``known_hosts``."""

from dataclasses import dataclass
from enum import StrEnum

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.server_access import validate_host, validate_port


class HostKeyStanding(StrEnum):
    """Compared with the user's ``known_hosts`` (ADR-0009).

    KNOWN: a scanned key matches. UNKNOWN: no entry for the host. CHANGED: the
    host has an entry and no scanned key matches; it is never accepted.
    """

    KNOWN = "known"
    UNKNOWN = "unknown"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class HostKey:
    """A public host key as shown by a server; ``fingerprint`` is SHA256:..."""

    host: str
    port: int
    key_type: str
    fingerprint: str

    def __post_init__(self) -> None:
        validate_host("HostKey", self.host)
        validate_port("HostKey", self.port)
        require_text("HostKey", "key_type", self.key_type)
        require_text("HostKey", "fingerprint", self.fingerprint)


@dataclass(frozen=True, slots=True)
class HostKeyCheck:
    """The result of reading the host key and comparing it with ``known_hosts``."""

    standing: HostKeyStanding
    host_key: HostKey | None
