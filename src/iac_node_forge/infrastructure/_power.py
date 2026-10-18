"""Operation knowledge shared by the provider adapters (power and provision).

How an Operation is created, failed and ended is the same for every adapter;
what a provider's answer means is not, and stays in the adapter.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from iac_node_forge.application.errors import ProviderError
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.operation import (
    Operation,
    OperationId,
    OperationState,
    bound_detail,
)
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration

type Clock = Callable[[], datetime]
"""Gives the current timezone-aware time; a test replaces it."""

_NO_REASON = "the provider gave no reason"


def utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


def required_value(desired: DesiredConfiguration, name: str) -> str:
    """Return the provider value ``name``, or raise ProviderError naming it."""
    value = desired.value(name)
    if value is None:
        raise ProviderError(f"the required value {name!r} is missing")
    return value


def running_operation(
    configuration: ProviderConfiguration,
    server_identifier: str | None,
    action: PowerAction,
    started_at: datetime,
    provider_reference: str | None,
) -> Operation:
    """Return a new operation in state RUNNING with a fresh identifier."""
    return Operation(
        operation_id=OperationId(uuid.uuid4().hex),
        configuration_id=configuration.configuration_id,
        server_identifier=server_identifier,
        action=action,
        state=OperationState.RUNNING,
        started_at=started_at,
        provider_reference=provider_reference,
    )


def failed_operation(
    configuration: ProviderConfiguration,
    server_identifier: str | None,
    action: PowerAction,
    started_at: datetime,
    reason: str,
) -> Operation:
    """Return a new operation that ended at once in state FAILED with a reason."""
    return running_operation(
        configuration, server_identifier, action, started_at, None
    ).ended(OperationState.FAILED, started_at, bound_detail(reason) or _NO_REASON)


def unknown_operation(
    configuration: ProviderConfiguration,
    server_identifier: str | None,
    action: PowerAction,
    started_at: datetime,
    reason: str,
) -> Operation:
    """Return a new operation that ended at once in state UNKNOWN with a reason.

    For a request whose answer was lost: it may have reached the provider.
    """
    return running_operation(
        configuration, server_identifier, action, started_at, None
    ).ended(OperationState.UNKNOWN, started_at, bound_detail(reason) or _NO_REASON)


def end_operation(
    operation: Operation, state: OperationState, clock: Clock, detail: str | None
) -> Operation:
    """End a running operation now; the end time is never before the start."""
    finished_at = max(clock(), operation.started_at)
    return operation.ended(state, finished_at, bound_detail(detail or "") or None)
