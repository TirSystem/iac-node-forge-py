"""The record of one transfer: what was copied, skipped, replaced and verified."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.errors import (
    InvalidTransferReportError,
    NaiveTimestampError,
)
from iac_node_forge.domain.manifest import ManifestDifference
from iac_node_forge.domain.provider_configuration import ConfigurationId

MAX_NOTE_LENGTH = 500


class TransferDirection(StrEnum):
    """Server to PC (backup) or PC to server (restore)."""

    BACKUP = "backup"
    RESTORE = "restore"


@dataclass(frozen=True, slots=True)
class OverwriteEntry:
    """An item on the server that a restore replaces and does not keep."""

    path: str
    size: int
    modified_at: datetime

    def __post_init__(self) -> None:
        require_text("OverwriteEntry", "path", self.path)
        if self.size < 0:
            raise InvalidTransferReportError("OverwriteEntry.size is negative")
        if self.modified_at.tzinfo is None:
            raise NaiveTimestampError("OverwriteEntry.modified_at needs a timezone")


@dataclass(frozen=True, slots=True)
class SkippedEntry:
    """A device, socket, pipe or refused member that was not copied."""

    path: str
    reason: str

    def __post_init__(self) -> None:
        require_text("SkippedEntry", "path", self.path)
        require_text("SkippedEntry", "reason", self.reason)


@dataclass(frozen=True, slots=True)
class TransferReport:
    """What a transfer did and whether the copy equals its source.

    ``verified`` is true only when the manifests were equal. The report holds
    identifiers, counts, paths and bounded text; it has no field that can hold a
    secret.
    """

    direction: TransferDirection
    configuration_id: ConfigurationId
    server_identifier: str
    started_at: datetime
    finished_at: datetime
    file_count: int
    link_count: int
    byte_count: int
    skipped: tuple[SkippedEntry, ...]
    overwritten: tuple[OverwriteEntry, ...]
    differences: tuple[ManifestDifference, ...]
    verified: bool
    note: str | None = None
    report_file: str | None = None

    def __post_init__(self) -> None:
        require_text("TransferReport", "configuration_id", self.configuration_id)
        require_text("TransferReport", "server_identifier", self.server_identifier)
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise NaiveTimestampError("TransferReport times need a timezone")
        if self.finished_at < self.started_at:
            raise InvalidTransferReportError("TransferReport ends before it starts")
        if min(self.file_count, self.link_count, self.byte_count) < 0:
            raise InvalidTransferReportError("TransferReport counts are negative")
        if self.verified and self.differences:
            raise InvalidTransferReportError("a verified report has no differences")
        if self.note is not None and len(self.note) > MAX_NOTE_LENGTH:
            raise InvalidTransferReportError("TransferReport.note is too long")
