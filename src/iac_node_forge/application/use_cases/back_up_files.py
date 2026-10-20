"""UC-016: copy files from a server to the local PC and verify the copy."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from iac_node_forge.application.errors import RemotePathNotFoundError, TransferError
from iac_node_forge.application.ports import (
    FileTransferPort,
    LocalFilesystemPort,
    RemoteReadPort,
)
from iac_node_forge.application.transfer_dto import (
    TransferOutcome,
    TransferPreview,
    TransferResult,
)
from iac_node_forge.application.use_cases._transfer_support import (
    Clock,
    TransferRun,
    outcome_of,
    preview_from,
    refused_paths,
    utc_now,
)
from iac_node_forge.application.use_cases.remote_access_resolver import (
    RemoteAccessResolver,
)
from iac_node_forge.domain.errors import DomainError
from iac_node_forge.domain.remote_path import ROOT, validate_remote_path
from iac_node_forge.domain.server_access import RemoteAccess
from iac_node_forge.domain.transfer_report import SkippedEntry, TransferDirection

MARGIN_PERCENT = 10
MARGIN_BYTES = 16 * 1024 * 1024
"""The room kept free beyond the data: 10 percent of it and 16 MiB."""


@dataclass(frozen=True, slots=True)
class _Checked:
    preview: TransferPreview
    access: RemoteAccess | None = None


def required_room(total_bytes: int) -> int:
    """Return the free bytes a backup of ``total_bytes`` needs: data plus margin."""
    return total_bytes + total_bytes * MARGIN_PERCENT // 100 + MARGIN_BYTES


class BackUpFiles:
    """Previews and makes a backup of server paths into a local directory.

    Only reads run on the server (the reader port and one ``pull``), so a backup
    changes nothing there. The copy is compared with the source by manifest and
    is reported as completed only when they are equal.
    """

    def __init__(
        self,
        resolver: RemoteAccessResolver,
        reader: RemoteReadPort,
        transfer: FileTransferPort,
        filesystem: LocalFilesystemPort,
        clock: Clock = utc_now,
    ) -> None:
        self._resolver = resolver
        self._reader = reader
        self._transfer = transfer
        self._filesystem = filesystem
        self._clock = clock

    def preview_backup(
        self,
        configuration: str,
        server_identifier: str,
        remote_paths: Sequence[str],
        destination: str,
        accepted_fingerprint: str | None,
    ) -> TransferPreview:
        """Check everything and read the size; copy and create nothing.

        READY carries the host, the key, the size, the room free and the room
        required; any other outcome says why a backup cannot be made. A key
        shown for the first time is written to known_hosts only when
        ``accepted_fingerprint`` equals its fingerprint. Raises the use case
        and provider errors of the reads.
        """
        return self._check(
            configuration,
            server_identifier,
            tuple(remote_paths),
            destination,
            accepted_fingerprint,
        ).preview

    def request_backup(
        self,
        configuration: str,
        server_identifier: str,
        remote_paths: Sequence[str],
        destination: str,
    ) -> TransferResult:
        """Copy the paths under ``destination``, compare, and save the report.

        Every refusal of the preview is returned before anything is copied. The
        host key must already be known; a fingerprint is accepted only by the
        preview.
        """
        paths = tuple(remote_paths)
        checked = self._check(
            configuration, server_identifier, paths, destination, None
        )
        preview = checked.preview
        if preview.outcome is not TransferOutcome.READY or checked.access is None:
            return TransferResult(
                preview.outcome, preview.reason, None, preview.host_key
            )
        configuration_id = preview.configuration_id
        assert configuration_id is not None  # READY means the configuration is known
        run = TransferRun(
            self._filesystem,
            TransferDirection.BACKUP,
            configuration_id,
            preview.server_identifier,
            self._clock(),
            Path(destination),
            preview.host_key,
        )
        return self._copy(run, checked.access, paths)

    def _copy(
        self, run: TransferRun, access: RemoteAccess, paths: tuple[str, ...]
    ) -> TransferResult:
        names = tuple(path[1:] for path in paths)
        try:
            source = self._reader.manifest(access, ROOT, names)
        except TransferError as error:
            outcome, reason = outcome_of(error)
            return TransferResult(outcome, reason, None, run.host_key)
        skipped: list[SkippedEntry] = []
        try:
            skipped = self._transfer.pull(access, paths, run.report_directory)
        except TransferError as error:
            copy = self._filesystem.manifest_under(run.report_directory, names)
            return run.stopped(self._clock(), outcome_of(error)[1], copy, skipped)
        copy = self._filesystem.manifest_under(run.report_directory, names)
        # A link the pull refused was never extracted: it is listed, not compared.
        left_out = refused_paths(skipped)
        return run.compared(
            self._clock(), source.without(left_out), copy.without(left_out), skipped
        )

    def _check(
        self,
        configuration: str,
        server_identifier: str,
        paths: tuple[str, ...],
        destination: str,
        accepted_fingerprint: str | None,
    ) -> _Checked:
        def stop(outcome: TransferOutcome, reason: str) -> _Checked:
            return _Checked(
                TransferPreview(
                    outcome,
                    reason,
                    TransferDirection.BACKUP,
                    server_identifier=server_identifier,
                    paths=paths,
                    destination=destination,
                )
            )

        invalid = _invalid_reason(paths)
        if invalid is not None:
            return stop(TransferOutcome.INVALID_PATH, invalid)
        directory = Path(destination)
        if not self._filesystem.is_new_or_empty(directory):
            return stop(
                TransferOutcome.DESTINATION_NOT_EMPTY,
                f"the destination {destination!r} exists and is not an empty directory",
            )
        resolution = self._resolver.resolve(
            configuration, server_identifier, accepted_fingerprint
        )
        preview = preview_from(
            resolution, TransferDirection.BACKUP, server_identifier, paths, destination
        )
        if resolution.outcome is not TransferOutcome.READY or resolution.access is None:
            return _Checked(preview)
        return self._with_room(preview, resolution.access, directory)

    def _with_room(
        self, preview: TransferPreview, access: RemoteAccess, directory: Path
    ) -> _Checked:
        def stop(outcome: TransferOutcome, reason: str) -> _Checked:
            return _Checked(replace(preview, outcome=outcome, reason=reason))

        try:
            total = self._reader.total_size(access, preview.paths)
        except RemotePathNotFoundError as error:
            missing = self._first_missing(access, preview.paths)
            where = (
                f"{missing!r} does not exist on the server" if missing else str(error)
            )
            return stop(TransferOutcome.PATH_NOT_FOUND, where)
        except TransferError as error:
            outcome, reason = outcome_of(error)
            return stop(outcome, reason)
        free = self._filesystem.free_bytes(directory)
        required = required_room(total)
        sized = replace(
            preview, total_bytes=total, free_bytes=free, required_bytes=required
        )
        if free < required:
            return _Checked(
                replace(
                    sized,
                    outcome=TransferOutcome.NO_ROOM,
                    reason=f"the destination has {free} bytes free and the backup "
                    f"needs {required} ({total} bytes of data plus a margin of "
                    f"{MARGIN_PERCENT} percent and {MARGIN_BYTES} bytes)",
                )
            )
        return _Checked(sized, access)

    def _first_missing(
        self, access: RemoteAccess, paths: tuple[str, ...]
    ) -> str | None:
        for path in paths:
            try:
                self._reader.total_size(access, (path,))
            except RemotePathNotFoundError:
                return path
            except TransferError:
                continue
        return None


def _invalid_reason(paths: tuple[str, ...]) -> str | None:
    """Return why the remote paths cannot be backed up, or None."""
    if not paths:
        return "no remote path was given"
    for path in paths:
        try:
            validate_remote_path("remote path", path)
        except DomainError as error:
            return f"{error}"
        if path == ROOT:
            return "the root '/' cannot be backed up as one path; name its directories"
    for first in paths:
        for second in paths:
            if first != second and second.startswith(first + "/"):
                return f"{second!r} is inside {first!r}; name each data once"
    if len(set(paths)) != len(paths):
        return "a remote path is given twice"
    return None
