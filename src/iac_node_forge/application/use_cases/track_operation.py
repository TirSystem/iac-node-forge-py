"""UC-006: follow an operation to its end and keep its outcome."""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from iac_node_forge.application.dto import OperationSummary
from iac_node_forge.application.errors import (
    CredentialStoreError,
    ProviderError,
    UseCaseError,
)
from iac_node_forge.application.ports import (
    ConfigurationStore,
    OperationStore,
    ProviderResolver,
    ServerPowerPort,
    ServerProvisionPort,
    ServerRemovalPort,
)
from iac_node_forge.application.use_cases._configuration_lookup import (
    find_configuration,
)
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

type Sleep = Callable[[float], None]
"""Waits for the given number of seconds; a test replaces it."""

DEFAULT_TIME_LIMIT_SECONDS = 120.0
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
TIME_LIMIT_PASSED = "time limit passed"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TrackOperation:
    """Polls the provider for an operation and records how it ended."""

    def __init__(
        self,
        providers: ProviderResolver,
        configurations: ConfigurationStore,
        operations: OperationStore,
        clock: Clock = _utc_now,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._providers = providers
        self._configurations = configurations
        self._operations = operations
        self._clock = clock
        self._sleep = sleep

    def follow_operation(
        self,
        operation_id: str,
        time_limit: float = DEFAULT_TIME_LIMIT_SECONDS,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> OperationSummary:
        """Poll until the operation ends or ``time_limit`` seconds have passed.

        An operation that is not running is returned as recorded. A limit
        that passes, or a provider that cannot be asked, ends the operation as
        UNKNOWN with the reason; the power request is never repeated. The
        ended operation is saved once. Raises OperationNotFoundError when no
        operation has the identifier.
        """
        operation = self._operations.load(OperationId(operation_id))
        if operation.has_ended:
            return OperationSummary.of(operation)
        ended = self._poll(operation, time_limit, poll_interval)
        self._operations.save(ended)
        return OperationSummary.of(ended)

    def show_operation(self, operation_id: str) -> OperationSummary:
        """Return the recorded operation; raises OperationNotFoundError."""
        return OperationSummary.of(self._operations.load(OperationId(operation_id)))

    def _poll(
        self, operation: Operation, time_limit: float, poll_interval: float
    ) -> Operation:
        deadline = self._clock() + timedelta(seconds=max(time_limit, 0.0))
        current = operation
        try:
            target = find_configuration(
                self._configurations, operation.configuration_id
            )
            reader = self._reader(target, current)
            while True:
                current = reader.operation_state(target, current)
                if current.has_ended:
                    return current
                remaining = (deadline - self._clock()).total_seconds()
                if remaining <= 0:
                    return self._unknown(current, TIME_LIMIT_PASSED)
                self._sleep(min(max(poll_interval, 0.0), remaining))
        except (ProviderError, CredentialStoreError, UseCaseError) as error:
            reason = bound_detail(str(error)) or type(error).__name__
            return self._unknown(current, reason)

    def _reader(
        self, target: ProviderConfiguration, operation: Operation
    ) -> ServerPowerPort | ServerProvisionPort | ServerRemovalPort:
        """The port that reads the operation, by its action."""
        if operation.action is PowerAction.PROVISION:
            return self._providers.provision_for(target)
        if operation.action is PowerAction.REMOVE:
            return self._providers.removal_for(target)
        return self._providers.power_for(target)

    def _unknown(self, operation: Operation, reason: str) -> Operation:
        finished_at = max(self._clock(), operation.started_at)
        return operation.ended(OperationState.UNKNOWN, finished_at, reason)
