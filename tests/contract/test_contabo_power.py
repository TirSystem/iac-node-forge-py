"""Power behaviours specific to the Contabo adapter, against the mock API."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from iac_node_forge.application.errors import ProviderError, ServerNotFoundError
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.operation import Operation, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.infrastructure.contabo import ContaboProvider
from tests.contract.conftest import CONTABO_VALUES, contabo_configuration_for
from tests.contract.mock_contabo import ACTIONS, MockContaboServer

T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


class Clock:
    """A settable clock for the restart settle time."""

    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def configuration(mock_contabo: MockContaboServer) -> ProviderConfiguration:
    return contabo_configuration_for(mock_contabo.address)


def provider_with(
    mock: MockContaboServer, settle: float = 30.0, clock: Clock | None = None
) -> ContaboProvider:
    return ContaboProvider(
        Credentials(CONTABO_VALUES),
        auth_url=mock.auth_url,
        timeout=2.0,
        restart_settle_seconds=settle,
        clock=clock or Clock(),
    )


@pytest.mark.parametrize(
    ("action", "verb"),
    [
        (PowerAction.START, "start"),
        (PowerAction.SHUTDOWN, "shutdown"),
        (PowerAction.STOP, "stop"),
        (PowerAction.RESTART, "restart"),
    ],
)
def test_action_posts_to_the_action_path_of_the_instance(
    mock_contabo: MockContaboServer,
    configuration: ProviderConfiguration,
    action: PowerAction,
    verb: str,
) -> None:
    operation = provider_with(mock_contabo).request_power(configuration, "100", action)

    assert operation.state is OperationState.RUNNING
    assert [(r.method, r.path) for r in mock_contabo.write_requests] == [
        ("POST", f"/v1/compute/instances/100/actions/{verb}")
    ]
    assert verb in ACTIONS


def test_there_is_no_provider_reference_because_contabo_has_no_task(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    operation = provider_with(mock_contabo).request_power(
        configuration, "100", PowerAction.STOP
    )

    assert operation.provider_reference is None


def test_every_request_has_its_own_uuid4_request_id(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    provider = provider_with(mock_contabo)
    first = provider.request_power(configuration, "100", PowerAction.STOP)
    provider.operation_state(configuration, first)
    provider.request_power(configuration, "101", PowerAction.START)

    ids = [r.request_id for r in mock_contabo.api_requests]

    # Per request: lookup GET and power POST; then the status GET.
    assert len(ids) == 5
    assert len(set(ids)) == 5
    assert all(uuid.UUID(str(i)).version == 4 for i in ids)


def test_token_is_renewed_once_after_401_on_an_operation_read(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    provider = provider_with(mock_contabo)
    operation = provider.request_power(configuration, "100", PowerAction.STOP)
    mock_contabo.state.token_expires_after = 1

    result = provider.operation_state(configuration, operation)

    assert result.state is OperationState.RUNNING
    assert mock_contabo.state.tokens_issued == 2


def test_token_is_renewed_once_after_401_on_the_power_post(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    provider = provider_with(mock_contabo)
    provider.list_servers(configuration)
    mock_contabo.state.token_expires_after = 2

    operation = provider.request_power(configuration, "100", PowerAction.STOP)

    assert operation.state is OperationState.RUNNING
    assert mock_contabo.state.tokens_issued == 2
    assert len(mock_contabo.write_requests) == 2  # refused once, then accepted


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_provider_failure_gives_failed_operation_with_the_status(
    mock_contabo: MockContaboServer,
    configuration: ProviderConfiguration,
    status: int,
) -> None:
    mock_contabo.state.power_forced_status = status

    operation = provider_with(mock_contabo).request_power(
        configuration, "100", PowerAction.STOP
    )

    assert operation.state is OperationState.FAILED
    assert operation.detail is not None
    assert str(status) in operation.detail
    for value in CONTABO_VALUES.values():
        assert value not in operation.detail


def test_token_failure_gives_failed_operation_and_no_power_post(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    mock_contabo.state.forced_status = 500

    operation = provider_with(mock_contabo).request_power(
        configuration, "100", PowerAction.STOP
    )

    assert operation.state is OperationState.FAILED
    assert mock_contabo.write_requests == []


def test_unknown_instance_raises_not_found(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    with pytest.raises(ServerNotFoundError):
        provider_with(mock_contabo).request_power(
            configuration, "999", PowerAction.START
        )


@pytest.mark.parametrize("identifier", ["abc", "1/../2", "", "-1"])
def test_non_numeric_identifier_raises_not_found_and_sends_nothing(
    mock_contabo: MockContaboServer,
    configuration: ProviderConfiguration,
    identifier: str,
) -> None:
    with pytest.raises(ServerNotFoundError):
        provider_with(mock_contabo).request_power(
            configuration, identifier, PowerAction.START
        )

    assert mock_contabo.state.requests == []


def test_start_is_running_until_the_status_equals_running(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    mock_contabo.state.interim_status = "provisioning"
    mock_contabo.state.transition_reads = 2
    provider = provider_with(mock_contabo)
    operation = provider.request_power(configuration, "101", PowerAction.START)

    states = []
    for _ in range(4):
        operation = provider.operation_state(configuration, operation)
        states.append(operation.state)

    assert states == [
        OperationState.RUNNING,
        OperationState.RUNNING,
        OperationState.SUCCEEDED,
        OperationState.SUCCEEDED,
    ]
    assert operation.finished_at is not None


@pytest.mark.parametrize("action", [PowerAction.SHUTDOWN, PowerAction.STOP])
def test_shutdown_and_stop_succeed_when_the_status_equals_stopped(
    mock_contabo: MockContaboServer,
    configuration: ProviderConfiguration,
    action: PowerAction,
) -> None:
    mock_contabo.state.transition_reads = 1
    provider = provider_with(mock_contabo)
    operation = provider.request_power(configuration, "100", action)

    first = provider.operation_state(configuration, operation)
    second = provider.operation_state(configuration, first)

    assert first.state is OperationState.RUNNING
    assert second.state is OperationState.SUCCEEDED


def test_status_error_gives_failed_with_the_status_in_the_detail(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    mock_contabo.state.transition_reads = 0
    mock_contabo.state.transition_fails = True
    provider = provider_with(mock_contabo)
    operation = provider.request_power(configuration, "100", PowerAction.STOP)

    result = provider.operation_state(configuration, operation)

    assert result.state is OperationState.FAILED
    assert result.detail is not None
    assert "error" in result.detail


@pytest.mark.parametrize(
    "status", ["provisioning", "installing", "unknown", "rescue", "running"]
)
def test_a_status_that_is_not_the_target_keeps_a_stop_running(
    mock_contabo: MockContaboServer,
    configuration: ProviderConfiguration,
    status: str,
) -> None:
    mock_contabo.state.instances[0]["status"] = status
    provider = provider_with(mock_contabo)
    operation = provider.request_power(configuration, "100", PowerAction.STOP)
    mock_contabo.state.transitions.clear()

    result = provider.operation_state(configuration, operation)

    assert result.state is OperationState.RUNNING
    assert result.finished_at is None


def _restart_at(
    mock: MockContaboServer,
    configuration: ProviderConfiguration,
    settle: float,
    seconds_after_request: float,
    status: str,
) -> Operation:
    clock = Clock()
    provider = provider_with(mock, settle=settle, clock=clock)
    operation = provider.request_power(configuration, "100", PowerAction.RESTART)
    mock.state.transitions.clear()
    mock.state.instances[0]["status"] = status
    clock.now = T0 + timedelta(seconds=seconds_after_request)
    return provider.operation_state(configuration, operation)


def test_restart_is_running_before_the_settle_time_even_when_running(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    result = _restart_at(mock_contabo, configuration, 30.0, 10.0, "running")

    assert result.state is OperationState.RUNNING


def test_restart_succeeds_after_the_settle_time_with_the_limit_in_the_detail(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    result = _restart_at(mock_contabo, configuration, 30.0, 30.0, "running")

    assert result.state is OperationState.SUCCEEDED
    assert result.detail is not None
    assert "not be observed" in result.detail


def test_restart_stays_running_after_the_settle_time_when_not_running(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    result = _restart_at(mock_contabo, configuration, 30.0, 60.0, "stopped")

    assert result.state is OperationState.RUNNING


def test_restart_with_status_error_fails_at_once(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    result = _restart_at(mock_contabo, configuration, 30.0, 1.0, "error")

    assert result.state is OperationState.FAILED


def test_reading_an_instance_that_is_gone_raises_not_found(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    provider = provider_with(mock_contabo)
    operation = provider.request_power(configuration, "100", PowerAction.STOP)
    mock_contabo.state.instances.clear()

    with pytest.raises(ServerNotFoundError):
        provider.operation_state(configuration, operation)


def test_server_error_on_the_status_read_raises_provider_error(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    provider = provider_with(mock_contabo)
    operation = provider.request_power(configuration, "100", PowerAction.STOP)
    mock_contabo.state.forced_status = 500

    with pytest.raises(ProviderError):
        provider.operation_state(configuration, operation)


def test_only_get_the_four_power_posts_and_the_token_post_are_sent(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    provider = provider_with(mock_contabo, settle=0.0)
    for action in PowerAction:
        operation = provider.request_power(configuration, "100", action)
        provider.operation_state(configuration, operation)

    assert mock_contabo.methods_used == {"GET", "POST"}
    assert {r.path.rsplit("/", 1)[1] for r in mock_contabo.write_requests} == ACTIONS
    assert all(r.method == "POST" for r in mock_contabo.write_requests)
    assert len(mock_contabo.token_requests) == 1


def test_power_capabilities_are_declared(
    mock_contabo: MockContaboServer, configuration: ProviderConfiguration
) -> None:
    names = {
        c.name for c in provider_with(mock_contabo).discover_capabilities(configuration)
    }

    assert {
        "start_server",
        "shutdown_server",
        "stop_server",
        "restart_server",
    } <= names
