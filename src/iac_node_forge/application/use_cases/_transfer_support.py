"""What the backup and restore use cases share: the clock, errors and the report."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from iac_node_forge.application.errors import (
    ConnectionFailedError,
    HostKeyChangedError,
    HostKeyUnknownError,
    PermissionDeniedError,
    RemotePathNotFoundError,
    TransferError,
)
from iac_node_forge.application.ports import LocalFilesystemPort
from iac_node_forge.application.transfer_dto import (
    AccessResolution,
    TransferOutcome,
    TransferPreview,
    TransferResult,
)
from iac_node_forge.domain.host_key import HostKey
from iac_node_forge.domain.manifest import DifferenceKind, Manifest, ManifestDifference
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.transfer_report import (
    MAX_NOTE_LENGTH,
    OverwriteEntry,
    SkippedEntry,
    TransferDirection,
    TransferReport,
)

type Clock = Callable[[], datetime]
"""Gives the current timezone-aware time; a test replaces it."""

REFUSED_PREFIX = "refused:"
_MAX_DIFFERENCES_IN_REASON = 5


def utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


def outcome_of(error: TransferError) -> tuple[TransferOutcome, str]:
    """Return the outcome a transfer error stands for, with its bounded text."""
    text = str(error)
    if isinstance(error, HostKeyChangedError):
        return TransferOutcome.HOST_KEY_CHANGED, text
    if isinstance(error, HostKeyUnknownError):
        return TransferOutcome.HOST_KEY_UNKNOWN, text
    if isinstance(error, RemotePathNotFoundError):
        return TransferOutcome.PATH_NOT_FOUND, text
    if isinstance(error, ConnectionFailedError | PermissionDeniedError):
        return TransferOutcome.CONNECTION_FAILED, text
    return TransferOutcome.TRANSFER_FAILED, text


def refused_paths(skipped: Iterable[SkippedEntry]) -> list[str]:
    """Return the relative paths of the members the extraction refused."""
    return [
        entry.path.lstrip("/")
        for entry in skipped
        if entry.reason.startswith(REFUSED_PREFIX)
    ]


def describe_differences(differences: tuple[ManifestDifference, ...]) -> str:
    """Return a short sentence that names the first differences."""
    shown = ", ".join(
        f"{difference.path} ({difference.kind.value})"
        for difference in differences[:_MAX_DIFFERENCES_IN_REASON]
    )
    more = len(differences) - _MAX_DIFFERENCES_IN_REASON
    tail = f" and {more} more" if more > 0 else ""
    return (
        f"the copy differs from the source in {len(differences)} path(s): {shown}{tail}"
    )


def preview_from(
    resolution: AccessResolution,
    direction: TransferDirection,
    server_identifier: str,
    paths: tuple[str, ...],
    destination: str,
) -> TransferPreview:
    """Return the preview with what the resolver found; the checks add the rest."""
    access = resolution.access
    server = resolution.server
    configuration = resolution.configuration
    return TransferPreview(
        resolution.outcome,
        resolution.reason,
        direction,
        configuration.configuration_id if configuration else None,
        configuration.display_name if configuration else None,
        server_identifier,
        server.name if server else None,
        server.state if server else None,
        resolution.managed,
        access.host if access else None,
        access.port if access else None,
        access.user if access else None,
        resolution.host_key,
        paths,
        destination,
    )


@dataclass(frozen=True, slots=True)
class TransferRun:
    """One transfer that has started: what its report and result are made of."""

    filesystem: LocalFilesystemPort
    direction: TransferDirection
    configuration_id: ConfigurationId
    server_identifier: str
    started_at: datetime
    report_directory: Path
    host_key: HostKey | None
    overwritten: tuple[OverwriteEntry, ...] = ()

    def compared(
        self,
        finished_at: datetime,
        source: Manifest,
        copy: Manifest,
        skipped: list[SkippedEntry],
        *,
        keeps_existing: bool = False,
    ) -> TransferResult:
        """Report a finished copy: COMPLETED only when the manifests are equal.

        With ``keeps_existing`` (a restore adds and overwrites, never deletes)
        a path only in the copy is not a failure: it is listed, counted in the
        note and left in place.
        """
        differences = source.differences_in(copy)
        blocking = tuple(d for d in differences if d.kind is not DifferenceKind.EXTRA)
        if not keeps_existing:
            blocking = differences
        left = len(differences) - len(blocking)
        note = (
            f"{left} item(s) on the server are not in the restored data and were "
            "left in place"
            if left and not blocking
            else None
        )
        report, unsaved = self._saved(
            finished_at, copy, skipped, differences, note=note, verified=not blocking
        )
        if not blocking:
            return TransferResult(
                TransferOutcome.COMPLETED, unsaved, report, self.host_key
            )
        reason = describe_differences(blocking)
        return TransferResult(
            TransferOutcome.VERIFICATION_FAILED,
            f"{reason}; {unsaved}" if unsaved else reason,
            report,
            self.host_key,
        )

    def stopped(
        self,
        finished_at: datetime,
        reason: str,
        copy: Manifest,
        skipped: list[SkippedEntry],
    ) -> TransferResult:
        """Report a copy that stopped; the report says it is incomplete."""
        note = f"incomplete: {reason}"[:MAX_NOTE_LENGTH]
        report, unsaved = self._saved(
            finished_at, copy, skipped, (), note=note, verified=False
        )
        text = f"{reason}; {unsaved}" if unsaved else reason
        return TransferResult(
            TransferOutcome.TRANSFER_FAILED, text, report, self.host_key
        )

    def _saved(
        self,
        finished_at: datetime,
        copy: Manifest,
        skipped: list[SkippedEntry],
        differences: tuple[ManifestDifference, ...],
        note: str | None,
        verified: bool,
    ) -> tuple[TransferReport, str]:
        report = TransferReport(
            self.direction,
            self.configuration_id,
            self.server_identifier,
            self.started_at,
            max(finished_at, self.started_at),
            copy.file_count,
            copy.link_count,
            copy.byte_count,
            tuple(skipped),
            self.overwritten,
            differences,
            verified=verified,
            note=note,
        )
        try:
            saved = self.filesystem.save_report(self.report_directory, report)
        except OSError as error:
            detail = error.strerror or "I/O error"
            return report, f"the report could not be saved: {detail}"
        return replace(report, report_file=saved), ""
