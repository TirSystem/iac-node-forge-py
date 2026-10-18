"""UC-004: provision a server from a desired configuration (ADR-0007)."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from iac_node_forge.application.dto import (
    Confirmation,
    OperationSummary,
    ProvisionOutcome,
    ProvisionPreview,
    ProvisionResult,
)
from iac_node_forge.application.errors import (
    CredentialStoreError,
    OperationNotFoundError,
    ProviderError,
    ProvisionUnsupportedError,
    UseCaseError,
)
from iac_node_forge.application.ports import (
    ConfigurationStore,
    DesiredConfigurationStore,
    OperationStore,
    ProviderResolver,
    ProvisionIntentStore,
)
from iac_node_forge.application.use_cases._configuration_lookup import (
    find_configuration,
)
from iac_node_forge.application.use_cases.desired_configuration_validator import (
    DesiredConfigurationValidator,
)
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.operation import (
    Operation,
    OperationId,
    OperationState,
)
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.provision_intent import (
    IntentId,
    IntentState,
    ProvisionIntent,
)

type Clock = Callable[[], datetime]
"""Gives the current timezone-aware time; a test replaces it."""

type IntentIdFactory = Callable[[], str]
"""Gives a new intent identifier; a test replaces it."""

# The value names that place and that size a server, for the preview. They are
# the names the providers declare in their value rules (ADR-0007).
_TARGET_VALUES = ("node", "storage", "region")
_SIZE_VALUES = ("kind", "memory_mb", "cores", "disk_gb", "product", "period")

_STATE_UNREAD = "the state left at the provider could not be read"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_intent_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class _Checked:
    """What the checks found: the subject of a request and the outcome so far."""

    target: ProviderConfiguration
    desired_name: str
    server_name: str
    desired: DesiredConfiguration | None
    outcome: ProvisionOutcome
    reason: str = ""


class ProvisionServer:
    """Previews, requests and completes the creation of one server.

    The create request is the last step: every refusal is decided from reads,
    the confirmation and the intent, and the intent is saved before the
    request so a lost answer cannot be followed by a second creation.
    """

    def __init__(
        self,
        providers: ProviderResolver,
        configurations: ConfigurationStore,
        desired_configurations: DesiredConfigurationStore,
        intents: ProvisionIntentStore,
        operations: OperationStore,
        validator: DesiredConfigurationValidator,
        clock: Clock = _utc_now,
        new_intent_id: IntentIdFactory = _new_intent_id,
    ) -> None:
        self._providers = providers
        self._configurations = configurations
        self._desired = desired_configurations
        self._intents = intents
        self._operations = operations
        self._validator = validator
        self._clock = clock
        self._new_intent_id = new_intent_id

    def preview_provisioning(
        self, configuration: str, desired_name: str, server_name: str
    ) -> ProvisionPreview:
        """Check everything but the confirmation; send and record nothing.

        The outcome is READY when the creation can be asked for, with what
        will be created and the cost consequence, or the reason it cannot.
        ``server_name`` empty means the desired name is used.
        """
        checked = self._check(configuration, desired_name, server_name)
        return self._preview(checked)

    def request_provisioning(
        self,
        configuration: str,
        desired_name: str,
        server_name: str,
        confirmation: Confirmation,
    ) -> ProvisionResult:
        """Send one create request when every precondition holds, and record it.

        The outcome says why nothing was sent: the description is invalid or
        unsupported, the name exists at the provider, an unfinished intent
        holds the name, or the confirmation is missing. A provider that
        rejects the request yields REJECTED with a FAILED operation and the
        intent ends; the request is never repeated.
        """
        checked = self._check(configuration, desired_name, server_name)
        if checked.outcome is not ProvisionOutcome.READY or checked.desired is None:
            return ProvisionResult(checked.outcome, checked.reason)
        if not confirmation.given:
            return ProvisionResult(
                ProvisionOutcome.NOT_CONFIRMED, "the provisioning was not confirmed"
            )
        return self._send(checked.target, checked.desired, checked.server_name)

    def complete_provisioning(self, operation_id: str) -> ProvisionResult:
        """Record how a provision operation ended: the managed server or the cause.

        A succeeded operation that names its server records the server as
        managed and ends the intent. A failed one ends the intent and reports
        what the provider holds. An unknown or unfinished one changes nothing.
        """
        operation = self._provision_operation(operation_id)
        if isinstance(operation, ProvisionResult):
            return operation
        intent = self._intents.load_by_operation(operation.operation_id)
        if intent is None:
            return ProvisionResult(
                ProvisionOutcome.INVALID,
                f"operation {operation_id!r} has no provision intent",
            )
        summary = OperationSummary.of(operation)
        if operation.state is OperationState.RUNNING:
            return ProvisionResult(
                ProvisionOutcome.INCOMPLETE,
                "the operation is still running",
                summary,
                operation.server_identifier,
            )
        if operation.state is OperationState.UNKNOWN:
            return ProvisionResult(
                ProvisionOutcome.INCOMPLETE,
                "the outcome is unknown and the intent stays pending: "
                + (operation.detail or "no reason was given"),
                summary,
                operation.server_identifier,
            )
        if operation.state is OperationState.FAILED:
            return self._failed(operation, intent)
        return self._succeeded(operation, intent)

    def _provision_operation(self, operation_id: str) -> Operation | ProvisionResult:
        try:
            operation = self._operations.load(OperationId(operation_id))
        except OperationNotFoundError:
            return ProvisionResult(
                ProvisionOutcome.INVALID, f"no operation {operation_id!r} is recorded"
            )
        if operation.action is not PowerAction.PROVISION:
            return ProvisionResult(
                ProvisionOutcome.INVALID,
                f"operation {operation_id!r} is not a provision operation",
            )
        return operation

    def _succeeded(
        self, operation: Operation, intent: ProvisionIntent
    ) -> ProvisionResult:
        summary = OperationSummary.of(operation)
        if operation.server_identifier is None:
            return ProvisionResult(
                ProvisionOutcome.INCOMPLETE,
                "the operation succeeded but the provider named no server and "
                "the intent stays pending: "
                + (operation.detail or "no detail was given"),
                summary,
            )
        self._configurations.save_managed_server(
            ManagedServerRecord(
                operation.configuration_id,
                operation.server_identifier,
                self._clock(),
            )
        )
        self._end(intent)
        return ProvisionResult(
            ProvisionOutcome.COMPLETED,
            "",
            summary,
            operation.server_identifier,
        )

    def _failed(self, operation: Operation, intent: ProvisionIntent) -> ProvisionResult:
        self._end(intent)
        return ProvisionResult(
            ProvisionOutcome.REJECTED,
            operation.detail or "the provider gave no reason",
            OperationSummary.of(operation),
            operation.server_identifier,
            self._state_left(
                operation.configuration_id,
                intent.server_name,
                operation.server_identifier,
            ),
        )

    def _check(
        self, configuration: str, desired_name: str, server_name: str
    ) -> _Checked:
        target = find_configuration(self._configurations, configuration)
        name = server_name.strip() or desired_name
        desired = self._desired.load(target.configuration_id, desired_name)

        def stop(outcome: ProvisionOutcome, reason: str) -> _Checked:
            return _Checked(target, desired_name, name, desired, outcome, reason)

        if desired is None:
            return stop(
                ProvisionOutcome.INVALID,
                f"no desired configuration {desired_name!r} for "
                f"{target.display_name!r}",
            )
        if not self._validator.supports_provisioning(target):
            return stop(
                ProvisionOutcome.UNSUPPORTED,
                "the provider does not support provisioning (capability "
                "provision_server)",
            )
        problems = self._validator.check(target, desired)
        if problems:
            return stop(ProvisionOutcome.INVALID, "; ".join(problems))
        provider = self._providers.provider_for(target)
        if any(server.name == name for server in provider.list_servers(target)):
            return stop(
                ProvisionOutcome.NAME_EXISTS,
                f"a server named {name!r} already exists at the provider",
            )
        pending = self._intents.find_pending(target.configuration_id, name)
        if pending is not None:
            known = (
                f"; its operation is {pending.operation_id}"
                if pending.operation_id
                else ""
            )
            return stop(
                ProvisionOutcome.INTENT_PENDING,
                f"an earlier request to create {name!r} is not finished{known}",
            )
        return stop(ProvisionOutcome.READY, "")

    def _preview(self, checked: _Checked) -> ProvisionPreview:
        target, desired = checked.target, checked.desired
        cost = ""
        if desired is not None and checked.outcome is ProvisionOutcome.READY:
            provision = self._providers.provision_for(target)
            cost = provision.cost_consequence(target, desired) or ""
        values = desired.values if desired else ()
        return ProvisionPreview(
            checked.outcome,
            target.provider_kind,
            target.configuration_id,
            target.display_name,
            checked.desired_name,
            checked.server_name,
            _named(desired, _TARGET_VALUES),
            desired.image if desired else "",
            _named(desired, _SIZE_VALUES),
            values,
            cost,
            checked.reason,
        )

    def _send(
        self,
        target: ProviderConfiguration,
        desired: DesiredConfiguration,
        server_name: str,
    ) -> ProvisionResult:
        provision = self._providers.provision_for(target)
        intent = ProvisionIntent(
            IntentId(self._new_intent_id()),
            target.configuration_id,
            desired.desired_name,
            server_name,
            desired.image,
            desired.values,
            IntentState.PENDING,
            self._clock(),
            reserved_identifier=provision.reserve_identifier(target),
        )
        self._intents.save(intent)
        try:
            operation = provision.request_provision(
                target, desired, server_name, intent.reserved_identifier
            )
        except ProvisionUnsupportedError as error:
            self._end(intent)
            return ProvisionResult(ProvisionOutcome.UNSUPPORTED, str(error))
        self._operations.save(operation)
        summary = OperationSummary.of(operation)
        if operation.state is OperationState.FAILED:
            self._intents.save(
                replace(
                    intent,
                    state=IntentState.ENDED,
                    operation_id=operation.operation_id,
                )
            )
            return ProvisionResult(
                ProvisionOutcome.REJECTED,
                operation.detail or "the provider gave no reason",
                summary,
                operation.server_identifier,
                self._state_left(
                    target.configuration_id, server_name, operation.server_identifier
                ),
            )
        self._intents.save(replace(intent, operation_id=operation.operation_id))
        return ProvisionResult(
            ProvisionOutcome.REQUESTED,
            "",
            summary,
            operation.server_identifier,
        )

    def _end(self, intent: ProvisionIntent) -> None:
        if intent.state is IntentState.PENDING:
            self._intents.save(replace(intent, state=IntentState.ENDED))

    def _state_left(
        self,
        configuration_id: str,
        server_name: str,
        server_identifier: str | None,
    ) -> str:
        """Say what the provider holds after a failure; never raises."""
        try:
            target = find_configuration(self._configurations, configuration_id)
            servers = self._providers.provider_for(target).list_servers(target)
        except (ProviderError, CredentialStoreError, UseCaseError):
            return _STATE_UNREAD
        for server in servers:
            if server.server_identifier == server_identifier or (
                server.name == server_name
            ):
                return (
                    f"server {server.server_identifier!r} named {server.name!r} "
                    f"exists in state {server.state}"
                )
        return f"no server named {server_name!r} was found at the provider"


def _named(desired: DesiredConfiguration | None, names: tuple[str, ...]) -> str:
    if desired is None:
        return ""
    pairs = (
        f"{name} {value}"
        for name in names
        if (value := desired.value(name)) is not None
    )
    return ", ".join(pairs)
