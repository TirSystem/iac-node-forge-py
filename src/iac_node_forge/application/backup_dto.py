"""Plain results of the backup and restore of a whole server (ADR-0010)."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from iac_node_forge.application.dto import OperationSummary
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.provider_kind import ProviderKind


class BackupOutcome(Enum):
    """How a preview, a request, a completion or a listing of a backup ended.

    READY is the answer of a preview and means every precondition but the
    confirmation holds. REQUESTED means a backup request was sent and its
    operation is recorded. COMPLETED means the operation succeeded and the
    backup is recorded. INCOMPLETE means the operation is unfinished or unknown,
    or succeeded without the backup being located. REJECTED means the provider
    refused the request or the operation ended as failed. LISTED answers a
    listing. In every other outcome no request was sent (NOT_FOUND is also the
    answer for an operation that is not a backup).
    """

    READY = "ready"
    REQUESTED = "requested"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    REJECTED = "rejected"
    UNSUPPORTED = "unsupported"
    NOT_FOUND = "not_found"
    STORAGE_REQUIRED = "storage_required"
    NO_BACKUP_STORAGE = "no_backup_storage"
    STORAGE_UNSUITABLE = "storage_unsuitable"
    NOT_AVAILABLE = "not_available"
    NOT_CONFIRMED = "not_confirmed"
    LISTED = "listed"


class RestoreOutcome(Enum):
    """How a preview, a request or a completion of a restore ended.

    The first six mean what they mean for a removal. BACKUP_NOT_FOUND is a
    backup that is not listed or an alias that is not unique, BACKUP_MISMATCH a
    backup of another guest, RUNNING_REFUSED a Proxmox VE guest that must be shut
    down first, NOT_AVAILABLE a cancelled server. In UNSUPPORTED, NOT_FOUND (also
    the answer for an operation that is not a restore), BACKUP_NOT_FOUND,
    BACKUP_MISMATCH, RUNNING_REFUSED, NOT_AVAILABLE, NOT_CONFIRMED and
    IDENTIFIER_MISMATCH no request was sent.
    """

    READY = "ready"
    REQUESTED = "requested"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    REJECTED = "rejected"
    UNSUPPORTED = "unsupported"
    NOT_FOUND = "not_found"
    BACKUP_NOT_FOUND = "backup_not_found"
    BACKUP_MISMATCH = "backup_mismatch"
    RUNNING_REFUSED = "running_refused"
    NOT_AVAILABLE = "not_available"
    NOT_CONFIRMED = "not_confirmed"
    IDENTIFIER_MISMATCH = "identifier_mismatch"


@dataclass(frozen=True, slots=True)
class BackupSummary:
    """A backup as presentation sees it; it holds no secret.

    ``operation_id`` is the operation that made it, where a record says so.
    """

    backup_id: str
    alias: str
    kind: BackupKind
    location: str
    created_at: datetime
    size_bytes: int | None
    label: str | None
    made_by_client: bool
    operation_id: str | None
    auto_delete_at: datetime | None

    @classmethod
    def of(cls, backup: Backup, operation_id: str | None = None) -> "BackupSummary":
        """Return the summary of a backup, with the operation that made it."""
        return cls(
            backup.backup_id,
            backup.alias(),
            backup.kind,
            backup.location,
            backup.created_at,
            backup.size_bytes,
            backup.label,
            backup.made_by_client,
            operation_id,
            backup.auto_delete_at,
        )


@dataclass(frozen=True, slots=True)
class BackupPreview:
    """What a backup would do, or why none can be made.

    ``storage`` is the storage chosen (None at a provider without storages),
    ``storages`` the ones offered when the user must choose, ``location`` where
    the backup would be kept and ``consequence`` is empty unless the outcome is
    READY.
    """

    outcome: BackupOutcome
    reason: str
    provider_name: str
    provider_kind: ProviderKind
    configuration_id: ConfigurationId
    server_identifier: str
    server_name: str | None = None
    server_state: str | None = None
    kind: BackupKind | None = None
    storage: str | None = None
    storages: tuple[str, ...] = ()
    location: str = ""
    consequence: str = ""


@dataclass(frozen=True, slots=True)
class BackupResult:
    """The answer to a request for, or the completion of, a backup.

    ``reason`` says why nothing was sent, why the provider refused, or what is
    unfinished; ``operation`` is set once a request was recorded;
    ``consequence`` is the text recorded with it; ``backup`` is the backup that
    was located and ``recorded`` says it is recorded; ``state_left`` says what the
    provider holds after a failure.
    """

    outcome: BackupOutcome
    reason: str = ""
    operation: OperationSummary | None = None
    consequence: str = ""
    backup: BackupSummary | None = None
    recorded: bool = False
    state_left: str | None = None


@dataclass(frozen=True, slots=True)
class BackupListing:
    """The backups of one server: what the provider lists and what it no longer
    lists of the backups this client recorded."""

    outcome: BackupOutcome
    reason: str
    provider_name: str
    configuration_id: ConfigurationId
    server_identifier: str
    kind: BackupKind | None = None
    backups: tuple[BackupSummary, ...] = ()
    gone_from_provider: tuple[BackupSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class RestorePreview:
    """What a restore would do, or why none can be made.

    ``consequence`` is empty unless the outcome is READY; ``deleted_backups``
    are the backups the restore deletes (the newer snapshots at Contabo).
    """

    outcome: RestoreOutcome
    reason: str
    provider_name: str
    provider_kind: ProviderKind
    configuration_id: ConfigurationId
    server_identifier: str
    server_name: str | None = None
    server_state: str | None = None
    managed: bool = False
    backup: BackupSummary | None = None
    consequence: str = ""
    deleted_backups: tuple[BackupSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """The answer to a request for, or the completion of, a restore.

    ``consequence`` is the text recorded with the operation and ``state_after``
    the state of the server read after the restore, None when it was not read.
    """

    outcome: RestoreOutcome
    reason: str = ""
    operation: OperationSummary | None = None
    consequence: str = ""
    state_after: str | None = None
