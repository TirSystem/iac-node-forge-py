"""Plain results of the server access and file transfer use cases (ADR-0009)."""

from dataclasses import dataclass
from enum import Enum

from iac_node_forge.domain.host_key import HostKey
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_access import RemoteAccess
from iac_node_forge.domain.transfer_report import (
    OverwriteEntry,
    TransferDirection,
    TransferReport,
)


class TransferOutcome(Enum):
    """How a preview or a transfer ended.

    READY is the answer of a preview: every precondition but the confirmation
    holds. COMPLETED means the copy was made and the manifests are equal.
    VERIFICATION_FAILED means they differ. TRANSFER_FAILED means the copy
    stopped. In every other outcome nothing was copied.
    """

    READY = "ready"
    COMPLETED = "completed"
    VERIFICATION_FAILED = "verification_failed"
    TRANSFER_FAILED = "transfer_failed"
    HOST_KEY_UNKNOWN = "host_key_unknown"
    HOST_KEY_CHANGED = "host_key_changed"
    CONNECTION_FAILED = "connection_failed"
    ACCESS_MISSING = "access_missing"
    SERVER_NOT_FOUND = "server_not_found"
    SERVER_NOT_RUNNING = "server_not_running"
    PATH_NOT_FOUND = "path_not_found"
    INVALID_PATH = "invalid_path"
    DESTINATION_NOT_EMPTY = "destination_not_empty"
    NO_ROOM = "no_room"
    NOT_CONFIRMED = "not_confirmed"
    IDENTIFIER_MISMATCH = "identifier_mismatch"


class AccessOutcome(Enum):
    """How defining the access of a server ended; only SAVED kept anything."""

    SAVED = "saved"
    INVALID = "invalid"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class ServerAccessView:
    """The access as kept, or why it was not kept; it holds no secret.

    ``host`` is None when the address the provider reports is used at the time
    of each transfer.
    """

    outcome: AccessOutcome
    reason: str
    configuration_id: ConfigurationId | None
    server_identifier: str
    host: str | None = None
    port: int | None = None
    user: str | None = None
    identity_hint: str | None = None


@dataclass(frozen=True, slots=True)
class AccessResolution:
    """What the resolver found: READY, or why the server cannot be reached.

    ``configuration`` is set once the configuration was found; ``server`` and
    ``managed`` once the provider answered; ``access`` once an address is known;
    ``host_key`` once the server showed one.
    """

    outcome: TransferOutcome
    reason: str
    configuration: ProviderConfiguration | None = None
    server: Server | None = None
    managed: bool = False
    access: RemoteAccess | None = None
    host_key: HostKey | None = None


@dataclass(frozen=True, slots=True)
class TransferPreview:
    """What a transfer will do, or why it cannot.

    The facts of the server, the connection and the host key are set as far as
    the checks got. ``required_bytes`` is the size plus the margin and is only
    set for a backup; ``overwrites`` is the Overwrite preview of a restore.
    """

    outcome: TransferOutcome
    reason: str
    direction: TransferDirection
    configuration_id: ConfigurationId | None = None
    provider_name: str | None = None
    server_identifier: str = ""
    server_name: str | None = None
    server_state: str | None = None
    managed: bool = False
    host: str | None = None
    port: int | None = None
    user: str | None = None
    host_key: HostKey | None = None
    paths: tuple[str, ...] = ()
    destination: str = ""
    total_bytes: int | None = None
    free_bytes: int | None = None
    required_bytes: int | None = None
    overwrites: tuple[OverwriteEntry, ...] = ()
    destination_exists: bool = False


@dataclass(frozen=True, slots=True)
class TransferResult:
    """The answer to a request for a backup or a restore.

    ``report`` is set once a copy was tried; ``host_key`` is the key shown when
    it is unknown or changed.
    """

    outcome: TransferOutcome
    reason: str = ""
    report: TransferReport | None = None
    host_key: HostKey | None = None
