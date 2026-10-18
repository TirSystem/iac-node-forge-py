"""Power behaviours every ServerPowerPort implementation shares.

Each test runs against FakePowerProvider, ProxmoxProvider plus its mock and
ContaboProvider plus its mock, which shows that the fake and both adapters
agree (Go criteria 2 and 5 of MIL-004). No variant is skipped.
"""

import logging

import pytest

from iac_node_forge.application.capabilities import POWER_ACTION_CAPABILITY
from iac_node_forge.application.errors import (
    PowerActionUnsupportedError,
    ServerNotFoundError,
)
from iac_node_forge.application.ports import ServerPowerPort
from iac_node_forge.domain.operation import (
    MAX_DETAIL_LENGTH,
    Operation,
    OperationState,
)
from iac_node_forge.domain.power_action import POWER_ACTIONS, PowerAction
from tests.contract.provider_under_test import ProviderUnderTest

MAX_POLLS = 20
STOPPED_SERVER = "101"
RUNNING_SERVER = "100"


def server_for(action: PowerAction) -> str:
    """A server of the shared data on which the action changes something."""
    return STOPPED_SERVER if action is PowerAction.START else RUNNING_SERVER


def follow(case: ProviderUnderTest, operation: Operation) -> list[Operation]:
    """Ask for the operation until it ends; the poll loop belongs to the use case."""
    seen: list[Operation] = []
    for _ in range(MAX_POLLS):
        operation = case.power.operation_state(case.configuration, operation)
        seen.append(operation)
        if operation.has_ended:
            break
    return seen


def declared(case: ProviderUnderTest, action: PowerAction) -> bool:
    names = {c.name for c in case.provider.discover_capabilities(case.configuration)}
    return POWER_ACTION_CAPABILITY[action] in names


def test_provider_satisfies_server_power_port(
    provider_under_test: ProviderUnderTest,
) -> None:
    assert isinstance(provider_under_test.power, ServerPowerPort)


@pytest.mark.parametrize("action", list(POWER_ACTIONS))
def test_declared_action_returns_running_operation_and_undeclared_is_unsupported(
    provider_under_test: ProviderUnderTest, action: PowerAction
) -> None:
    case = provider_under_test
    if not declared(case, action):
        with pytest.raises(PowerActionUnsupportedError):
            case.power.request_power(case.configuration, server_for(action), action)
        return

    operation = case.power.request_power(case.configuration, server_for(action), action)

    assert operation.state is OperationState.RUNNING
    assert operation.action is action
    assert operation.server_identifier == server_for(action)
    assert operation.configuration_id == case.configuration.configuration_id
    assert operation.started_at.tzinfo is not None
    assert operation.finished_at is None


@pytest.mark.parametrize("action", list(POWER_ACTIONS))
def test_following_a_running_operation_reaches_succeeded(
    provider_under_test: ProviderUnderTest, action: PowerAction
) -> None:
    case = provider_under_test
    operation = case.power.request_power(case.configuration, server_for(action), action)

    seen = follow(case, operation)

    final = seen[-1]
    assert final.state is OperationState.SUCCEEDED
    assert final.finished_at is not None
    assert final.finished_at >= final.started_at
    assert final.operation_id == operation.operation_id
    assert final.action is action
    assert len(seen) < MAX_POLLS


@pytest.mark.parametrize("action", [PowerAction.START, PowerAction.STOP])
def test_operation_is_reported_running_before_it_ends(
    provider_under_test: ProviderUnderTest, action: PowerAction
) -> None:
    case = provider_under_test
    operation = case.power.request_power(case.configuration, server_for(action), action)

    seen = follow(case, operation)

    assert seen[0].state is OperationState.RUNNING
    assert seen[0].finished_at is None


def test_an_ended_operation_is_returned_unchanged(
    provider_under_test: ProviderUnderTest,
) -> None:
    case = provider_under_test
    operation = case.power.request_power(
        case.configuration, RUNNING_SERVER, PowerAction.STOP
    )
    final = follow(case, operation)[-1]

    again = case.power.operation_state(case.configuration, final)

    assert again == final


def test_provider_rejection_gives_failed_operation_with_a_reason(
    provider_under_test: ProviderUnderTest,
) -> None:
    case = provider_under_test
    case.reject_power_requests()

    operation = case.power.request_power(
        case.configuration, RUNNING_SERVER, PowerAction.STOP
    )

    assert operation.state is OperationState.FAILED
    assert operation.detail
    assert len(operation.detail) <= MAX_DETAIL_LENGTH
    assert operation.finished_at is not None
    assert not [v for v in case.secret_values if v in operation.detail]


def test_operation_that_the_provider_ends_in_failure_is_failed_with_a_reason(
    provider_under_test: ProviderUnderTest,
) -> None:
    case = provider_under_test
    case.fail_operations()
    operation = case.power.request_power(
        case.configuration, RUNNING_SERVER, PowerAction.STOP
    )

    final = follow(case, operation)[-1]

    assert final.state is OperationState.FAILED
    assert final.detail
    assert final.finished_at is not None


def test_request_for_absent_server_raises_not_found_and_sends_no_write(
    provider_under_test: ProviderUnderTest,
) -> None:
    case = provider_under_test

    with pytest.raises(ServerNotFoundError):
        case.power.request_power(
            case.configuration, case.missing_identifier, PowerAction.START
        )

    assert case.write_requests() == []


def test_the_only_writes_received_are_the_four_power_requests(
    provider_under_test: ProviderUnderTest,
) -> None:
    case = provider_under_test

    for action in POWER_ACTIONS:
        operation = case.power.request_power(
            case.configuration, server_for(action), action
        )
        follow(case, operation)
    written = case.write_requests()

    assert len(written) == len(POWER_ACTIONS)
    assert set(written) <= case.allowed_write_requests
    assert len(set(written)) == len(POWER_ACTIONS)


def test_secret_values_never_appear_in_operations_errors_or_logs(
    provider_under_test: ProviderUnderTest, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    case = provider_under_test
    texts: list[str] = [repr(case.power)]
    operation = case.power.request_power(
        case.configuration, RUNNING_SERVER, PowerAction.STOP
    )
    for step in [operation, *follow(case, operation)]:
        texts.extend(_texts_of(step))
    with pytest.raises(ServerNotFoundError) as missing:
        case.power.request_power(
            case.configuration, case.missing_identifier, PowerAction.STOP
        )
    texts.append(str(missing.value))
    case.reject_power_requests()
    rejected = case.power.request_power(
        case.configuration, RUNNING_SERVER, PowerAction.STOP
    )
    texts.extend(_texts_of(rejected))
    texts.extend(record.getMessage() for record in caplog.records)

    leaked = [v for v in case.secret_values if any(v in text for text in texts)]
    assert texts
    assert not leaked


def _texts_of(operation: Operation) -> list[str]:
    return [
        repr(operation),
        str(operation),
        operation.detail or "",
        operation.provider_reference or "",
    ]


def test_provision_is_not_a_power_action_and_sends_nothing(
    provider_under_test: ProviderUnderTest,
) -> None:
    case = provider_under_test

    with pytest.raises(PowerActionUnsupportedError):
        case.power.request_power(
            case.configuration, RUNNING_SERVER, PowerAction.PROVISION
        )

    assert case.write_requests() == []
