"""UC-012: delete or cancel a server (ADR-0008)."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from iac_node_forge.application.capabilities import REMOVE_SERVER
from iac_node_forge.application.dto import (
    Confirmation,
    OperationSummary,
    RemovalOutcome,
    RemovalPreview,
    RemovalResult,
)
from iac_node_forge.application.errors import (
    CredentialStoreError,
    OperationNotFoundError,
    ProviderError,
    RemovalUnsupportedError,
    ServerNotFoundError,
    UseCaseError,
)
from iac_node_forge.application.ports import (
    ConfigurationStore,
    OperationStore,
    ProviderResolver,
    RemovalBlock,
)
from iac_node_forge.application.use_cases._configuration_lookup import (
    find_configuration,
)
from iac_node_forge.application.use_cases._confirmation import confirmation_refusal
from iac_node_forge.domain.operation import Operation, OperationId, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.server import Server

type Clock = Callable[[], datetime]
"""Gives the current timezone-aware time; a test replaces it."""

_STATE_UNREAD = "the state left at the provider could not be read"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _Checked:
    """The subject of a removal and the preview the checks led to."""

    target: ProviderConfiguration
    preview: RemovalPreview
    server: Server | None = None


class RemoveServer:
    """Previews, requests and completes the removal of one server.

    The removal request is the last step: every refusal is decided from reads
    and from the confirmation, so a refused request has sent and recorded
    nothing. A server is never stopped on the user's behalf, and a request that
    got no answer is recorded as UNKNOWN and never repeated.
    """

    def __init__(
        self,
        providers: ProviderResolver,
        configurations: ConfigurationStore,
        operations: OperationStore,
        clock: Clock = _utc_now,
    ) -> None:
        self._providers = providers
        self._configurations = configurations
        self._operations = operations
        self._clock = clock

    def preview_removal(
        self, configuration: str, server_identifier: str
    ) -> RemovalPreview:
        """Check everything but the confirmation; send and record nothing.

        The outcome is READY when the removal can be asked for, with what it
        does at this provider, or the reason it cannot. Raises the use case and
        provider errors of the reads (configuration not found, unreachable,
        rejected credentials).
        """
        return self._check(configuration, server_identifier).preview

    def request_removal(
        self,
        configuration: str,
        server_identifier: str,
        confirmation: Confirmation,
    ) -> RemovalResult:
        """Send one removal request when every precondition holds, and record it.

        The outcome says why nothing was sent: unsupported, not found, running,
        already removed, not confirmed, or the typed identifier differs from
        the provider's. A provider that rejects the request yields REJECTED with
        a FAILED operation; a lost answer yields REQUESTED with an UNKNOWN one.
        The request is never repeated.
        """
        checked = self._check(configuration, server_identifier)
        preview = checked.preview
        if preview.outcome is not RemovalOutcome.READY or checked.server is None:
            return RemovalResult(preview.outcome, preview.reason)
        refusal = _confirmation_refusal(preview, confirmation)
        if refusal is not None:
            return refusal
        return self._send(checked.target, checked.server, preview)

    def complete_removal(self, operation_id: str) -> RemovalResult:
        """Record how a removal operation ended: the managed record or the cause.

        A succeeded operation ends the managed-server record of its server, if
        there is one; a failed one reports what the provider holds; a running
        or unknown one changes nothing. Completing again changes nothing more.
        """
        operation = self._removal_operation(operation_id)
        if isinstance(operation, RemovalResult):
            return operation
        summary = OperationSummary.of(operation)
        consequence = operation.consequence or ""
        if operation.state is OperationState.RUNNING:
            return RemovalResult(
                RemovalOutcome.INCOMPLETE,
                "the operation is still running",
                summary,
                consequence,
            )
        if operation.state is OperationState.UNKNOWN:
            return RemovalResult(
                RemovalOutcome.INCOMPLETE,
                "the outcome is unknown and nothing was changed: "
                + (operation.detail or "no reason was given"),
                summary,
                consequence,
                self._state_left(operation),
            )
        if operation.state is OperationState.FAILED:
            return RemovalResult(
                RemovalOutcome.REJECTED,
                operation.detail or "the provider gave no reason",
                summary,
                consequence,
                self._state_left(operation),
            )
        return RemovalResult(
            RemovalOutcome.COMPLETED,
            "",
            summary,
            consequence,
            self._state_left(operation),
            self._end_managed(operation),
        )

    def _removal_operation(self, operation_id: str) -> Operation | RemovalResult:
        try:
            operation = self._operations.load(OperationId(operation_id))
        except OperationNotFoundError:
            return RemovalResult(
                RemovalOutcome.NOT_FOUND, f"no operation {operation_id!r} is recorded"
            )
        if operation.action is not PowerAction.REMOVE:
            return RemovalResult(
                RemovalOutcome.NOT_FOUND,
                f"operation {operation_id!r} is not a removal operation",
            )
        return operation

    def _end_managed(self, operation: Operation) -> bool:
        """End the active managed record the removal belongs to; say if it did."""
        identifier = operation.server_identifier
        if identifier is None:
            return False
        # A record made after the removal started is a new server that reuses
        # the identifier; completing an old removal again must not end it.
        active = [
            record
            for record in self._configurations.managed_servers(
                operation.configuration_id
            )
            if record.server_identifier == identifier
            and record.recorded_at <= operation.started_at
        ]
        if not active:
            return False
        self._configurations.end_managed_server(
            operation.configuration_id,
            identifier,
            max(self._clock(), operation.started_at),
            operation.operation_id,
        )
        return True

    def _check(self, configuration: str, server_identifier: str) -> _Checked:
        target = find_configuration(self._configurations, configuration)

        def stop(
            outcome: RemovalOutcome, reason: str, server: Server | None = None
        ) -> _Checked:
            return _Checked(
                target,
                RemovalPreview(
                    outcome,
                    reason,
                    target.display_name,
                    target.provider_kind,
                    target.configuration_id,
                    server.server_identifier if server else server_identifier,
                    server.name if server else None,
                    server.state if server else None,
                    self._is_managed(target, server) if server else False,
                ),
                server,
            )

        provider = self._providers.provider_for(target)
        if REMOVE_SERVER not in {
            c.name for c in provider.discover_capabilities(target)
        }:
            return stop(
                RemovalOutcome.UNSUPPORTED,
                "the provider does not support removing a server (capability "
                f"{REMOVE_SERVER})",
            )
        try:
            server = provider.get_server(target, server_identifier)
        except ServerNotFoundError:
            return stop(
                RemovalOutcome.NOT_FOUND,
                f"the provider has no server {server_identifier!r}",
            )
        removal = self._providers.removal_for(target)
        block = removal.removal_block(target, server)
        if block is RemovalBlock.RUNNING:
            return stop(
                RemovalOutcome.RUNNING_REFUSED,
                f"the server is {server.state} and must be shut down first; "
                "it is not stopped for you",
                server,
            )
        if block is RemovalBlock.ALREADY_REMOVED:
            return stop(
                RemovalOutcome.ALREADY_REMOVED,
                f"the server is already removed (state {server.state})",
                server,
            )
        ready = stop(RemovalOutcome.READY, "", server)
        return _Checked(
            target,
            replace(
                ready.preview,
                kind=removal.removal_kind(target),
                consequence=removal.consequence(target, server),
            ),
            server,
        )

    def _is_managed(self, target: ProviderConfiguration, server: Server) -> bool:
        managed_ids = {
            record.server_identifier
            for record in self._configurations.managed_servers(target.configuration_id)
        }
        return server.managed or server.server_identifier in managed_ids

    def _send(
        self, target: ProviderConfiguration, server: Server, preview: RemovalPreview
    ) -> RemovalResult:
        removal = self._providers.removal_for(target)
        try:
            sent = removal.request_removal(target, server)
        except RemovalUnsupportedError as error:
            return RemovalResult(RemovalOutcome.UNSUPPORTED, str(error))
        except ServerNotFoundError:
            return RemovalResult(
                RemovalOutcome.NOT_FOUND,
                f"the provider has no server {server.server_identifier!r}",
            )
        operation = replace(sent, consequence=preview.consequence)
        self._operations.save(operation)
        summary = OperationSummary.of(operation)
        if operation.state is OperationState.FAILED:
            return RemovalResult(
                RemovalOutcome.REJECTED,
                operation.detail or "the provider gave no reason",
                summary,
                preview.consequence,
                self._state_left(operation),
            )
        return RemovalResult(RemovalOutcome.REQUESTED, "", summary, preview.consequence)

    def _state_left(self, operation: Operation) -> str:
        """Say what the provider holds after the removal; never raises."""
        identifier = operation.server_identifier or ""
        try:
            target = find_configuration(
                self._configurations, operation.configuration_id
            )
            servers = self._providers.provider_for(target).list_servers(target)
        except (ProviderError, CredentialStoreError, UseCaseError):
            return _STATE_UNREAD
        held = next((s for s in servers if s.server_identifier == identifier), None)
        text = (
            f"server {identifier!r} no longer exists at the provider"
            if held is None
            else f"server {identifier!r} exists in state {held.state}"
        )
        if operation.state is OperationState.SUCCEEDED and operation.detail:
            return f"{text}; {operation.detail}"
        return text


def _confirmation_refusal(
    preview: RemovalPreview, confirmation: Confirmation
) -> RemovalResult | None:
    """Return the refusal when the confirmation is not complete and exact."""
    refusal = confirmation_refusal(
        confirmation, preview.server_identifier, preview.managed, "removal"
    )
    if refusal is None:
        return None
    outcome = (
        RemovalOutcome.IDENTIFIER_MISMATCH
        if refusal.mismatch
        else RemovalOutcome.NOT_CONFIRMED
    )
    return RemovalResult(outcome, refusal.reason)
