"""UC-017: copy local files to a server and verify the copy."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from iac_node_forge.application.dto import Confirmation
from iac_node_forge.application.errors import TransferError
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
from iac_node_forge.application.use_cases._confirmation import confirmation_refusal
from iac_node_forge.application.use_cases._transfer_support import (
    Clock,
    TransferRun,
    outcome_of,
    preview_from,
    utc_now,
)
from iac_node_forge.application.use_cases.remote_access_resolver import (
    RemoteAccessResolver,
)
from iac_node_forge.domain.errors import DomainError
from iac_node_forge.domain.manifest import Manifest
from iac_node_forge.domain.remote_path import (
    ROOT,
    validate_relative_path,
    validate_remote_path,
)
from iac_node_forge.domain.server_access import RemoteAccess
from iac_node_forge.domain.transfer_report import SkippedEntry, TransferDirection


@dataclass(frozen=True, slots=True)
class _Checked:
    preview: TransferPreview
    access: RemoteAccess | None = None
    paths: tuple[Path, ...] = ()
    names: tuple[str, ...] = ()


class RestoreFiles:
    """Previews and makes a restore of local paths onto a server.

    The only write on the server is ``push``, and it is reached only after the
    server identifier was typed exactly (twice for a server this client does not
    manage). A restore replaces what is at the destination and does not keep the
    previous version.
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

    def preview_restore(
        self,
        configuration: str,
        server_identifier: str,
        local_paths: Sequence[str],
        remote_destination: str,
        accepted_fingerprint: str | None,
    ) -> TransferPreview:
        """Check everything and list what would be replaced; write nothing.

        READY carries the host, the key, the size, whether the destination
        exists and the items it would replace. A key shown for the first time is
        written to known_hosts only when ``accepted_fingerprint`` equals its
        fingerprint. Raises the use case and provider errors of the reads.
        """
        return self._check(
            configuration,
            server_identifier,
            tuple(local_paths),
            remote_destination,
            accepted_fingerprint,
        ).preview

    def request_restore(
        self,
        configuration: str,
        server_identifier: str,
        local_paths: Sequence[str],
        remote_destination: str,
        report_directory: str,
        confirmation: Confirmation,
    ) -> TransferResult:
        """Copy the paths to the server when confirmed, compare, and save the report.

        Every refusal, the missing or wrong confirmation included, is returned
        before anything is written on the server. The host key must already be
        known; a fingerprint is accepted only by the preview.
        """
        checked = self._check(
            configuration,
            server_identifier,
            tuple(local_paths),
            remote_destination,
            None,
        )
        preview = checked.preview
        if preview.outcome is not TransferOutcome.READY or checked.access is None:
            return TransferResult(
                preview.outcome, preview.reason, None, preview.host_key
            )
        refusal = confirmation_refusal(
            confirmation, preview.server_identifier, preview.managed, "restore"
        )
        if refusal is not None:
            outcome = (
                TransferOutcome.IDENTIFIER_MISMATCH
                if refusal.mismatch
                else TransferOutcome.NOT_CONFIRMED
            )
            return TransferResult(outcome, refusal.reason, None, preview.host_key)
        configuration_id = preview.configuration_id
        assert configuration_id is not None  # READY means the configuration is known
        run = TransferRun(
            self._filesystem,
            TransferDirection.RESTORE,
            configuration_id,
            preview.server_identifier,
            self._clock(),
            Path(report_directory),
            preview.host_key,
            preview.overwrites,
        )
        return self._copy(run, checked, remote_destination)

    def _copy(
        self, run: TransferRun, checked: _Checked, destination: str
    ) -> TransferResult:
        access = checked.access
        assert access is not None  # the caller checked it
        source = self._filesystem.manifest_of_paths(checked.paths)
        skipped: list[SkippedEntry] = []
        try:
            skipped = self._transfer.push(access, checked.paths, destination)
            copy = self._reader.manifest(access, destination, checked.names)
        except TransferError as error:
            return run.stopped(
                self._clock(),
                outcome_of(error)[1],
                self._written(access, destination, checked.names),
                skipped,
            )
        return run.compared(self._clock(), source, copy, skipped, keeps_existing=True)

    def _written(
        self, access: RemoteAccess, destination: str, names: tuple[str, ...]
    ) -> Manifest:
        """Return what the server holds of the paths now, as far as it can be read."""
        try:
            return self._reader.manifest(access, destination, names)
        except TransferError:
            return Manifest(())

    def _check(
        self,
        configuration: str,
        server_identifier: str,
        local_paths: tuple[str, ...],
        destination: str,
        accepted_fingerprint: str | None,
    ) -> _Checked:
        def stop(outcome: TransferOutcome, reason: str) -> _Checked:
            return _Checked(
                TransferPreview(
                    outcome,
                    reason,
                    TransferDirection.RESTORE,
                    server_identifier=server_identifier,
                    paths=local_paths,
                    destination=destination,
                )
            )

        invalid = _invalid_reason(local_paths, destination)
        if invalid is not None:
            return stop(TransferOutcome.INVALID_PATH, invalid)
        paths = tuple(Path(path) for path in local_paths)
        missing = self._filesystem.missing(paths)
        if missing:
            return stop(
                TransferOutcome.PATH_NOT_FOUND,
                f"{str(missing[0])!r} does not exist on this computer",
            )
        resolution = self._resolver.resolve(
            configuration, server_identifier, accepted_fingerprint
        )
        preview = preview_from(
            resolution,
            TransferDirection.RESTORE,
            server_identifier,
            local_paths,
            destination,
        )
        if resolution.outcome is not TransferOutcome.READY or resolution.access is None:
            return _Checked(preview)
        names = tuple(path.name for path in paths)
        return self._with_overwrites(preview, resolution.access, paths, names)

    def _with_overwrites(
        self,
        preview: TransferPreview,
        access: RemoteAccess,
        paths: tuple[Path, ...],
        names: tuple[str, ...],
    ) -> _Checked:
        destination = preview.destination
        try:
            overwrites = self._reader.existing(access, destination, names)
            exists = self._destination_exists(access, destination)
        except TransferError as error:
            outcome, reason = outcome_of(error)
            return _Checked(replace(preview, outcome=outcome, reason=reason))
        ready = replace(
            preview,
            total_bytes=self._filesystem.size(paths),
            overwrites=tuple(overwrites),
            destination_exists=exists,
        )
        return _Checked(ready, access, paths, names)

    def _destination_exists(self, access: RemoteAccess, destination: str) -> bool:
        if destination == ROOT:
            return True
        parent, _, name = destination.rpartition("/")
        return bool(self._reader.existing(access, parent or ROOT, (name,)))


def _invalid_reason(local_paths: tuple[str, ...], destination: str) -> str | None:
    """Return why the paths cannot be restored, or None."""
    if not local_paths:
        return "no local path was given"
    try:
        validate_remote_path("remote destination", destination)
    except DomainError as error:
        return str(error)
    names = [Path(path).name for path in local_paths]
    for name in names:
        try:
            validate_relative_path("final name", name)
        except DomainError:
            return "a local path has no final name a server can take"
    if len(set(names)) != len(names):
        return "two local paths have the same final name"
    return None
