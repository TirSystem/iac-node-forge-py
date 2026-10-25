"""UC-019: restore a whole server from a backup at its provider (ADR-0010)."""

from dataclasses import dataclass, replace

from iac_node_forge.application.backup_dto import (
    BackupSummary,
    RestoreOutcome,
    RestorePreview,
    RestoreResult,
)
from iac_node_forge.application.capabilities import RESTORE_SERVER
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
    ConfigurationStore,
    OperationStore,
    ProviderResolver,
)
from iac_node_forge.application.use_cases._backup_lookup import (
    declares,
    resolve_backup,
)
from iac_node_forge.application.use_cases._configuration_lookup import (
    find_configuration,
)
from iac_node_forge.application.use_cases._confirmation import confirmation_refusal
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.operation import (
    Operation,
    OperationId,
    OperationState,
    bound_detail,
)
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.server import Server

STATE_RECORD_PREFIX = "server state after the restore: "
"""Starts the part of an operation's detail that records the state of the server."""

_BLOCK_OUTCOME: dict[BackupBlock, RestoreOutcome] = {
    BackupBlock.RUNNING: RestoreOutcome.RUNNING_REFUSED,
    BackupBlock.MISMATCH: RestoreOutcome.BACKUP_MISMATCH,
}
"""The outcome of a block; any other block means the server is not available."""


@dataclass(frozen=True, slots=True)
class _Checked:
    """The subject of a restore and the preview the checks led to."""

    target: ProviderConfiguration
    preview: RestorePreview
    server: Server | None = None
    backup: Backup | None = None


class RestoreServer:
    """Previews, requests and completes the restore of one server from a backup.

    A restore cannot be undone, so it follows the protection of a removal: the
    identifier is always typed, a server this client does not manage needs it
    typed again, the consequence is stated first and recorded, and a running
    Proxmox VE guest is refused and never stopped. Every refusal is decided from
    reads and from the confirmation, so a refused request has sent and recorded
    nothing; the one request is never repeated.
    """

    def __init__(
        self,
        providers: ProviderResolver,
        configurations: ConfigurationStore,
        operations: OperationStore,
    ) -> None:
        self._providers = providers
        self._configurations = configurations
        self._operations = operations

    def preview_restore(
        self, configuration: str, server_identifier: str, backup_id: str
    ) -> RestorePreview:
        """Check everything but the confirmation; send and record nothing.

        ``backup_id`` is the identifier of a backup the provider lists for the
        server, or its alias when that is unique. The outcome is READY with what
        the restore replaces and deletes, or the reason it cannot be asked for.
        """
        return self._check(configuration, server_identifier, backup_id).preview

    def request_restore(
        self,
        configuration: str,
        server_identifier: str,
        backup_id: str,
        confirmation: Confirmation,
    ) -> RestoreResult:
        """Send one restore request when every precondition holds, and record it.

        The outcome says why nothing was sent: unsupported, not found, no such
        backup, a mismatch, a running or unavailable server, not confirmed, or
        the typed identifier differs from the provider's. A provider that rejects
        the request yields REJECTED with a FAILED operation; a lost answer yields
        REQUESTED with an UNKNOWN one. The request is never repeated.
        """
        checked = self._check(configuration, server_identifier, backup_id)
        preview = checked.preview
        if (
            preview.outcome is not RestoreOutcome.READY
            or checked.server is None
            or checked.backup is None
        ):
            return RestoreResult(preview.outcome, preview.reason)
        refusal = confirmation_refusal(
            confirmation, preview.server_identifier, preview.managed, "restore"
        )
        if refusal is not None:
            outcome = (
                RestoreOutcome.IDENTIFIER_MISMATCH
                if refusal.mismatch
                else RestoreOutcome.NOT_CONFIRMED
            )
            return RestoreResult(outcome, refusal.reason)
        return self._send(checked.target, checked.server, checked.backup, preview)

    def complete_restore(self, operation_id: str) -> RestoreResult:
        """Read the state of the server a restore ended on and record it.

        A succeeded operation gets the state in its detail; a failed or unknown
        one reports the state left where the provider can be asked, and a running
        one changes nothing. Completing again changes nothing more.
        """
        operation = self._restore_operation(operation_id)
        if isinstance(operation, RestoreResult):
            return operation
        summary = OperationSummary.of(operation)
        consequence = operation.consequence or ""
        if operation.state is OperationState.RUNNING:
            return RestoreResult(
                RestoreOutcome.INCOMPLETE,
                "the operation is still running",
                summary,
                consequence,
            )
        state = self._state_of(operation)
        if operation.state is OperationState.UNKNOWN:
            return RestoreResult(
                RestoreOutcome.INCOMPLETE,
                "the outcome is unknown and nothing was changed by this client: "
                + (operation.detail or "no reason was given"),
                summary,
                consequence,
                state,
            )
        if operation.state is OperationState.FAILED:
            return RestoreResult(
                RestoreOutcome.REJECTED,
                operation.detail or "the provider gave no reason",
                summary,
                consequence,
                state,
            )
        return self._record_state(operation, consequence, state)

    def _restore_operation(self, operation_id: str) -> Operation | RestoreResult:
        try:
            operation = self._operations.load(OperationId(operation_id))
        except OperationNotFoundError:
            return RestoreResult(
                RestoreOutcome.NOT_FOUND, f"no operation {operation_id!r} is recorded"
            )
        if operation.action is not PowerAction.RESTORE:
            return RestoreResult(
                RestoreOutcome.NOT_FOUND,
                f"operation {operation_id!r} is not a restore operation",
            )
        return operation

    def _record_state(
        self, operation: Operation, consequence: str, state: str | None
    ) -> RestoreResult:
        """Put the state read into the detail of the succeeded operation, once."""
        recorded = operation
        reason = "the state of the server could not be read"
        if state is not None:
            reason = ""
            if not (operation.detail or "").startswith(STATE_RECORD_PREFIX):
                kept = f"; {operation.detail}" if operation.detail else ""
                recorded = replace(
                    operation, detail=bound_detail(STATE_RECORD_PREFIX + state + kept)
                )
                self._operations.save(recorded)
        return RestoreResult(
            RestoreOutcome.COMPLETED,
            reason,
            OperationSummary.of(recorded),
            consequence,
            state,
        )

    def _state_of(self, operation: Operation) -> str | None:
        """Read the state of the server now; None when it cannot be read."""
        identifier = operation.server_identifier or ""
        try:
            target = find_configuration(
                self._configurations, operation.configuration_id
            )
            provider = self._providers.provider_for(target)
            return provider.get_server(target, identifier).state
        except (ProviderError, CredentialStoreError, UseCaseError):
            return None

    def _check(
        self, configuration: str, server_identifier: str, backup_id: str
    ) -> _Checked:
        target = find_configuration(self._configurations, configuration)

        def stop(
            outcome: RestoreOutcome,
            reason: str,
            server: Server | None = None,
            backup: Backup | None = None,
        ) -> _Checked:
            return _Checked(
                target,
                RestorePreview(
                    outcome,
                    reason,
                    target.display_name,
                    target.provider_kind,
                    target.configuration_id,
                    server.server_identifier if server else server_identifier,
                    server.name if server else None,
                    server.state if server else None,
                    self._is_managed(target, server) if server else False,
                    BackupSummary.of(backup) if backup else None,
                ),
                server,
                backup,
            )

        provider = self._providers.provider_for(target)
        if not declares(provider, target, RESTORE_SERVER):
            return stop(
                RestoreOutcome.UNSUPPORTED,
                "the provider does not support restoring a server (capability "
                f"{RESTORE_SERVER})",
            )
        try:
            server = provider.get_server(target, server_identifier)
        except ServerNotFoundError:
            return stop(
                RestoreOutcome.NOT_FOUND,
                f"the provider has no server {server_identifier!r}",
            )
        port = self._providers.backup_for(target)
        backup = resolve_backup(port.list_backups(target, server), backup_id)
        if backup is None:
            return stop(
                RestoreOutcome.BACKUP_NOT_FOUND,
                f"the provider lists no backup {backup_id!r} for the server, or "
                "the alias fits more than one; 'server full-backups' lists them",
                server,
            )
        block = port.restore_block(target, server, backup)
        if block is not None:
            return stop(
                _BLOCK_OUTCOME.get(block, RestoreOutcome.NOT_AVAILABLE),
                _block_reason(block, server),
                server,
                backup,
            )
        effect = port.restore_effect(target, server, backup)
        ready = stop(RestoreOutcome.READY, "", server, backup)
        return _Checked(
            target,
            replace(
                ready.preview,
                consequence=effect.consequence,
                deleted_backups=tuple(
                    BackupSummary.of(b) for b in effect.deleted_backups
                ),
            ),
            server,
            backup,
        )

    def _is_managed(self, target: ProviderConfiguration, server: Server) -> bool:
        managed_ids = {
            record.server_identifier
            for record in self._configurations.managed_servers(target.configuration_id)
        }
        return server.managed or server.server_identifier in managed_ids

    def _send(
        self,
        target: ProviderConfiguration,
        server: Server,
        backup: Backup,
        preview: RestorePreview,
    ) -> RestoreResult:
        port = self._providers.backup_for(target)
        try:
            sent = port.request_restore(target, server, backup)
        except BackupUnsupportedError as error:
            return RestoreResult(RestoreOutcome.UNSUPPORTED, str(error))
        except ServerNotFoundError:
            return RestoreResult(
                RestoreOutcome.NOT_FOUND,
                f"the provider has no server {server.server_identifier!r}",
            )
        operation = replace(sent, consequence=preview.consequence)
        self._operations.save(operation)
        summary = OperationSummary.of(operation)
        if operation.state is OperationState.FAILED:
            return RestoreResult(
                RestoreOutcome.REJECTED,
                operation.detail or "the provider gave no reason",
                summary,
                preview.consequence,
                self._state_of(operation),
            )
        return RestoreResult(RestoreOutcome.REQUESTED, "", summary, preview.consequence)


def _block_reason(block: BackupBlock, server: Server) -> str:
    if block is BackupBlock.RUNNING:
        return (
            f"the server is {server.state} and must be shut down first; it is "
            "not stopped for you"
        )
    if block is BackupBlock.MISMATCH:
        return "the backup is not of this server (another type of guest or server)"
    return f"the server is not available for a restore (state {server.state})"
