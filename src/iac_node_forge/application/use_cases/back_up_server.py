"""UC-018: back up a whole server at its provider (ADR-0010)."""

from dataclasses import dataclass, replace

from iac_node_forge.application.backup_dto import (
    BackupOutcome,
    BackupPreview,
    BackupResult,
    BackupSummary,
)
from iac_node_forge.application.capabilities import BACKUP_SERVER
from iac_node_forge.application.dto import Confirmation, OperationSummary
from iac_node_forge.application.errors import (
    BackupUnsupportedError,
    CredentialStoreError,
    OperationNotFoundError,
    ProviderError,
    ServerNotFoundError,
    UseCaseError,
)
from iac_node_forge.application.ports import (
    BackupBlock,
    BackupStore,
    ConfigurationStore,
    OperationStore,
    ProviderResolver,
)
from iac_node_forge.application.use_cases._backup_lookup import (
    declares,
    summary_of_record,
)
from iac_node_forge.application.use_cases._configuration_lookup import (
    find_configuration,
)
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.backup_record import BackupRecord
from iac_node_forge.domain.operation import Operation, OperationId, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.server import Server

_STATE_UNREAD = "the backups held by the provider could not be read"
_NOT_LOCATED = (
    "the operation succeeded but the provider lists no backup that carries it, so "
    "the backup could not be located and nothing was recorded"
)
_BLOCK_OUTCOME: dict[BackupBlock, BackupOutcome] = {
    BackupBlock.NO_BACKUP_STORAGE: BackupOutcome.NO_BACKUP_STORAGE,
    BackupBlock.STORAGE_UNSUITABLE: BackupOutcome.STORAGE_UNSUITABLE,
}
"""The outcome of a block; any other block means the server is not available."""


@dataclass(frozen=True, slots=True)
class _Checked:
    """The subject of a backup and the preview the checks led to."""

    target: ProviderConfiguration
    preview: BackupPreview
    server: Server | None = None


class BackUpServer:
    """Previews, requests and completes the backup of one server.

    The backup request is the last step: every refusal is decided from reads and
    from the confirmation, so a refused request has sent and recorded nothing.
    A backup changes nothing on the server and deletes nothing, so the
    confirmation is a plain yes. A request that got no answer is recorded as
    UNKNOWN and never repeated.
    """

    def __init__(
        self,
        providers: ProviderResolver,
        configurations: ConfigurationStore,
        operations: OperationStore,
        backups: BackupStore,
    ) -> None:
        self._providers = providers
        self._configurations = configurations
        self._operations = operations
        self._backups = backups

    def preview_backup(
        self, configuration: str, server_identifier: str, storage: str | None
    ) -> BackupPreview:
        """Check everything but the confirmation; send and record nothing.

        The outcome is READY when the backup can be asked for, with its kind,
        the place it would be kept and what it does at this provider, or the
        reason it cannot. With ``storage`` None the only storage that accepts
        backups is used. Raises the use case and provider errors of the reads
        (configuration not found, unreachable, rejected credentials).
        """
        return self._check(configuration, server_identifier, storage).preview

    def request_backup(
        self,
        configuration: str,
        server_identifier: str,
        storage: str | None,
        confirmation: Confirmation,
    ) -> BackupResult:
        """Send one backup request when every precondition holds, and record it.

        The outcome says why nothing was sent: unsupported, not found, not
        available, the storage condition, or not confirmed. A provider that
        rejects the request yields REJECTED with a FAILED operation; a lost
        answer yields REQUESTED with an UNKNOWN one. The request is never
        repeated.
        """
        checked = self._check(configuration, server_identifier, storage)
        preview = checked.preview
        if preview.outcome is not BackupOutcome.READY or checked.server is None:
            return BackupResult(preview.outcome, preview.reason)
        if not confirmation.given:
            return BackupResult(
                BackupOutcome.NOT_CONFIRMED, "the backup was not confirmed"
            )
        return self._send(checked.target, checked.server, preview)

    def complete_backup(self, operation_id: str) -> BackupResult:
        """Record the backup an operation made, once the provider lists it.

        A succeeded operation whose backup the provider lists records the backup
        and says where it is kept; if the provider does not list it nothing is
        recorded and the result says so. A failed one reports what the provider
        holds; a running or unknown one changes nothing. Completing again
        records no second backup.
        """
        operation = self._backup_operation(operation_id)
        if isinstance(operation, BackupResult):
            return operation
        summary = OperationSummary.of(operation)
        consequence = operation.consequence or ""
        if operation.state is OperationState.RUNNING:
            return BackupResult(
                BackupOutcome.INCOMPLETE,
                "the operation is still running",
                summary,
                consequence,
            )
        if operation.state is OperationState.UNKNOWN:
            return BackupResult(
                BackupOutcome.INCOMPLETE,
                "the outcome is unknown and nothing was recorded: "
                + (operation.detail or "no reason was given"),
                summary,
                consequence,
                state_left=self._state_left(operation),
            )
        if operation.state is OperationState.FAILED:
            return BackupResult(
                BackupOutcome.REJECTED,
                operation.detail or "the provider gave no reason",
                summary,
                consequence,
                state_left=self._state_left(operation),
            )
        return self._record(operation, summary, consequence)

    def _backup_operation(self, operation_id: str) -> Operation | BackupResult:
        try:
            operation = self._operations.load(OperationId(operation_id))
        except OperationNotFoundError:
            return BackupResult(
                BackupOutcome.NOT_FOUND, f"no operation {operation_id!r} is recorded"
            )
        if operation.action is not PowerAction.BACKUP:
            return BackupResult(
                BackupOutcome.NOT_FOUND,
                f"operation {operation_id!r} is not a backup operation",
            )
        return operation

    def _record(
        self, operation: Operation, summary: OperationSummary, consequence: str
    ) -> BackupResult:
        """Locate the backup of a succeeded operation and record it once."""
        known = self._backups.load_by_operation(operation.operation_id)
        if known is not None:
            return BackupResult(
                BackupOutcome.COMPLETED,
                "",
                summary,
                consequence,
                summary_of_record(known),
                recorded=True,
            )
        try:
            located = self._locate(operation)
        except (ProviderError, CredentialStoreError, UseCaseError) as error:
            reason = f"the provider could not be asked to locate it: {error}"
            return BackupResult(BackupOutcome.INCOMPLETE, reason, summary, consequence)
        if located is None:
            return BackupResult(
                BackupOutcome.INCOMPLETE, _NOT_LOCATED, summary, consequence
            )
        self._backups.save(
            BackupRecord(
                operation.configuration_id,
                operation.server_identifier or "",
                located.backup_id,
                located.kind,
                located.location,
                located.created_at,
                operation.operation_id,
            )
        )
        return BackupResult(
            BackupOutcome.COMPLETED,
            "",
            summary,
            consequence,
            BackupSummary.of(located, operation.operation_id),
            recorded=True,
        )

    def _locate(self, operation: Operation) -> Backup | None:
        target = find_configuration(self._configurations, operation.configuration_id)
        return self._providers.backup_for(target).find_backup(target, operation)

    def _check(
        self, configuration: str, server_identifier: str, storage: str | None
    ) -> _Checked:
        target = find_configuration(self._configurations, configuration)

        def stop(
            outcome: BackupOutcome,
            reason: str,
            server: Server | None = None,
            storages: tuple[str, ...] = (),
        ) -> _Checked:
            return _Checked(
                target,
                BackupPreview(
                    outcome,
                    reason,
                    target.display_name,
                    target.provider_kind,
                    target.configuration_id,
                    server.server_identifier if server else server_identifier,
                    server.name if server else None,
                    server.state if server else None,
                    storage=storage,
                    storages=storages,
                ),
                server,
            )

        provider = self._providers.provider_for(target)
        if not declares(provider, target, BACKUP_SERVER):
            return stop(
                BackupOutcome.UNSUPPORTED,
                "the provider does not support backing up a server (capability "
                f"{BACKUP_SERVER})",
            )
        try:
            server = provider.get_server(target, server_identifier)
        except ServerNotFoundError:
            return stop(
                BackupOutcome.NOT_FOUND,
                f"the provider has no server {server_identifier!r}",
            )
        port = self._providers.backup_for(target)
        offered = port.backup_storages(target, server)
        if storage is None and len(offered) > 1:
            return stop(
                BackupOutcome.STORAGE_REQUIRED,
                "several storages accept backups; name one with --storage: "
                + ", ".join(offered),
                server,
                offered,
            )
        chosen = storage if storage is not None else _only(offered)
        block = port.backup_block(target, server, chosen)
        if block is not None:
            return stop(
                _BLOCK_OUTCOME.get(block, BackupOutcome.NOT_AVAILABLE),
                _block_reason(block, server, chosen),
                server,
                offered,
            )
        kind = port.backup_kind(target)
        ready = stop(BackupOutcome.READY, "", server, offered)
        return _Checked(
            target,
            replace(
                ready.preview,
                kind=kind,
                storage=chosen,
                location=_place(kind, chosen),
                consequence=port.backup_consequence(target, server, chosen),
            ),
            server,
        )

    def _send(
        self, target: ProviderConfiguration, server: Server, preview: BackupPreview
    ) -> BackupResult:
        port = self._providers.backup_for(target)
        try:
            sent = port.request_backup(target, server, preview.storage)
        except BackupUnsupportedError as error:
            return BackupResult(BackupOutcome.UNSUPPORTED, str(error))
        except ServerNotFoundError:
            return BackupResult(
                BackupOutcome.NOT_FOUND,
                f"the provider has no server {server.server_identifier!r}",
            )
        operation = replace(sent, consequence=preview.consequence)
        self._operations.save(operation)
        summary = OperationSummary.of(operation)
        if operation.state is OperationState.FAILED:
            return BackupResult(
                BackupOutcome.REJECTED,
                operation.detail or "the provider gave no reason",
                summary,
                preview.consequence,
                state_left=self._state_left(operation),
            )
        return BackupResult(BackupOutcome.REQUESTED, "", summary, preview.consequence)

    def _state_left(self, operation: Operation) -> str:
        """Say what the provider holds after the operation; never raises."""
        identifier = operation.server_identifier or ""
        try:
            target = find_configuration(
                self._configurations, operation.configuration_id
            )
            provider = self._providers.provider_for(target)
            held = self._providers.backup_for(target).list_backups(
                target, provider.get_server(target, identifier)
            )
        except (ProviderError, CredentialStoreError, UseCaseError):
            return _STATE_UNREAD
        return (
            f"the provider lists {len(held)} backup(s) of server {identifier!r}; "
            "none was recorded for this operation"
        )


def _only(offered: tuple[str, ...]) -> str | None:
    """The one storage on offer, or None when there is none to choose."""
    return offered[0] if len(offered) == 1 else None


def _place(kind: BackupKind, storage: str | None) -> str:
    if kind is BackupKind.ARCHIVE and storage is not None:
        return f"storage {storage}"
    return "the provider itself, as a snapshot"


def _block_reason(block: BackupBlock, server: Server, storage: str | None) -> str:
    if block is BackupBlock.NO_BACKUP_STORAGE:
        return "no storage of the server's node accepts backups"
    if block is BackupBlock.STORAGE_UNSUITABLE:
        named = f"storage {storage!r}" if storage else "the storage"
        return f"{named} cannot hold a backup of this server at this provider"
    return f"the server cannot be backed up now (state {server.state}): {block.value}"
