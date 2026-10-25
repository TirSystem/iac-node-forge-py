"""One backup of a server as the provider reports it."""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.errors import InvalidBackupError, NaiveTimestampError

GUEST_TYPES = ("lxc", "qemu")
"""The types of guest a Proxmox VE archive can hold."""

ALIAS_LENGTH = 8
"""Hexadecimal characters of the short local alias of a backup."""


@dataclass(frozen=True, slots=True)
class Backup:
    """A backup as the provider lists it (ADR-0010).

    ``backup_id`` is provider-specific text (a Proxmox VE ``volid`` or a Contabo
    ``snapshotId``). ``location`` says where it is kept. ``label`` is the first
    line of the notes of an archive or the name of a snapshot, ``guest_type`` is
    ``lxc`` or ``qemu`` for an archive, ``made_by_client`` says whether the
    marker of this client is on it and ``auto_delete_at`` is the time at which the
    provider deletes it, where it says so. It never holds a secret.
    """

    backup_id: str
    kind: BackupKind
    location: str
    created_at: datetime
    size_bytes: int | None = None
    label: str | None = None
    guest_type: str | None = None
    made_by_client: bool = False
    auto_delete_at: datetime | None = None

    def __post_init__(self) -> None:
        require_text("Backup", "backup_id", self.backup_id)
        require_text("Backup", "location", self.location)
        if self.label is not None:
            require_text("Backup", "label", self.label)
        if self.created_at.tzinfo is None:
            raise NaiveTimestampError("Backup.created_at needs a timezone")
        if self.auto_delete_at is not None and self.auto_delete_at.tzinfo is None:
            raise NaiveTimestampError("Backup.auto_delete_at needs a timezone")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise InvalidBackupError("Backup.size_bytes must not be negative")
        if self.guest_type is not None and self.guest_type not in GUEST_TYPES:
            raise InvalidBackupError(
                "Backup.guest_type must be one of " + ", ".join(GUEST_TYPES)
            )

    def alias(self) -> str:
        """Return the short local alias: the first eight hex characters of the
        SHA-256 of the identifier. Derived, never stored."""
        digest = hashlib.sha256(self.backup_id.encode()).hexdigest()
        return digest[:ALIAS_LENGTH]
