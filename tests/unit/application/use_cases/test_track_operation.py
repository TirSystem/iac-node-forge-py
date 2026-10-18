"""UC-006, OC-003: following and showing an operation through the fakes."""

from dataclasses import dataclass, fields

import pytest

from iac_node_forge.application.errors import (
    CredentialNotFoundError,
    OperationNotFoundError,
    ProviderAuthenticationError,
    ProviderUnreachableError,
)
from iac_node_forge.application.use_cases import TrackOperation
from iac_node_forge.domain.operation import (
    MAX_DETAIL_LENGTH,
    Operation,
    OperationState,
)
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.server import Server
from tests.unit.application.fakes import (
    FakeClock,
    FakeConfigurationStore,
    FakeOperationStore,
    FakePowerProvider,
    FakeProvider,
    FakeProviderResolver,
    FakeRemovalProvider,
)
from tests.unit.application.samples import LAB


class ErroringPower(FakePowerProvider):
    """A power port whose reading of an operation raises."""

    def __init__(self, error: Exception, clock: FakeClock) -> None:
        super().__init__(clock=clock)
        self._error = error

    def operation_state(
        self, configuration: ProviderConfiguration, operation: Operation
    ) -> Operation:
        raise self._error


@dataclass(slots=True)
class Setup:
    tracker: TrackOperation
    power: FakePowerProvider
    operations: FakeOperationStore
    clock: FakeClock
    running: Operation


def make(
    power: FakePowerProvider | None = None,
    clock: FakeClock | None = None,
    configured: bool = True,
    steps_to_finish: int = 3,
) -> Setup:
    fake_clock = clock or FakeClock()
    fake_power = power or FakePowerProvider(
        steps_to_finish=steps_to_finish, clock=fake_clock
    )
    store = FakeConfigurationStore()
    if configured:
        store.save(LAB)
    operations = FakeOperationStore()
    running = fake_power.request_power(LAB, "100", PowerAction.STOP)
    operations.save(running)
    operations.saved.clear()
    tracker = TrackOperation(
        FakeProviderResolver(FakeProvider(), power=fake_power),
        store,
        operations,
        clock=fake_clock,
        sleep=fake_clock.sleep,
    )
    return Setup(tracker, fake_power, operations, fake_clock, running)


def test_follow_polls_until_the_operation_succeeds_and_saves_it_once() -> None:
    setup = make(steps_to_finish=3)

    result = setup.tracker.follow_operation(setup.running.operation_id, 120, 2)

    assert result.state is OperationState.SUCCEEDED
    assert result.finished_at is not None
    assert setup.clock.sleeps == [2.0, 2.0]
    assert [o.state for o in setup.operations.saved] == [OperationState.SUCCEEDED]
    assert setup.power.requests == [("100", PowerAction.STOP)]


def test_follow_records_the_cause_when_the_operation_fails() -> None:
    setup = make()
    setup.power.make_operations_fail()

    result = setup.tracker.follow_operation(setup.running.operation_id, 120, 2)

    assert result.state is OperationState.FAILED
    assert result.detail == "the fake task failed"
    assert len(setup.operations.saved) == 1


def test_follow_ends_as_unknown_when_the_time_limit_passes() -> None:
    setup = make(steps_to_finish=1000)

    result = setup.tracker.follow_operation(setup.running.operation_id, 5, 2)

    assert result.state is OperationState.UNKNOWN
    assert result.detail == "time limit passed"
    assert result.finished_at is not None
    assert setup.clock.sleeps == [2.0, 2.0, 1.0]
    assert setup.power.requests == [("100", PowerAction.STOP)]
    assert [o.state for o in setup.operations.saved] == [OperationState.UNKNOWN]


def test_follow_with_a_zero_limit_reads_once_and_ends_unknown() -> None:
    setup = make(steps_to_finish=1000)

    result = setup.tracker.follow_operation(setup.running.operation_id, 0, 2)

    assert result.state is OperationState.UNKNOWN
    assert setup.clock.sleeps == []


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnreachableError("the provider timed out"),
        ProviderAuthenticationError("the provider answered 401"),
    ],
)
def test_follow_ends_as_unknown_with_the_reason_when_the_provider_errors(
    error: Exception,
) -> None:
    clock = FakeClock()
    setup = make(power=ErroringPower(error, clock), clock=clock)

    result = setup.tracker.follow_operation(setup.running.operation_id, 120, 2)

    assert result.state is OperationState.UNKNOWN
    assert result.detail == str(error)
    assert "Traceback" not in (result.detail or "")
    assert len(setup.operations.saved) == 1


def test_follow_bounds_a_long_provider_error_text() -> None:
    clock = FakeClock()
    error = ProviderUnreachableError("x" * 5000)
    setup = make(power=ErroringPower(error, clock), clock=clock)

    result = setup.tracker.follow_operation(setup.running.operation_id, 120, 2)

    assert result.detail is not None
    assert len(result.detail) <= MAX_DETAIL_LENGTH


def test_follow_ends_as_unknown_when_the_credentials_cannot_be_obtained() -> None:
    clock = FakeClock()
    power = FakePowerProvider(clock=clock)
    store = FakeConfigurationStore()
    store.save(LAB)
    operations = FakeOperationStore()
    running = power.request_power(LAB, "100", PowerAction.STOP)
    operations.save(running)

    class NoCredentials(FakeProviderResolver):
        def power_for(self, configuration: ProviderConfiguration) -> FakePowerProvider:
            raise CredentialNotFoundError("lab-reference")

    tracker = TrackOperation(
        NoCredentials(FakeProvider()),
        store,
        operations,
        clock=clock,
        sleep=clock.sleep,
    )

    result = tracker.follow_operation(running.operation_id, 120, 2)

    assert result.state is OperationState.UNKNOWN
    assert "lab-reference" in (result.detail or "")


def test_follow_ends_as_unknown_when_the_configuration_no_longer_exists() -> None:
    setup = make(configured=False)

    result = setup.tracker.follow_operation(setup.running.operation_id, 120, 2)

    assert result.state is OperationState.UNKNOWN
    assert setup.power.requests == [("100", PowerAction.STOP)]


def test_follow_returns_an_ended_operation_unchanged() -> None:
    setup = make()
    ended = setup.running.ended(
        OperationState.SUCCEEDED, setup.running.started_at, None
    )
    setup.operations.save(ended)
    setup.operations.saved.clear()

    result = setup.tracker.follow_operation(setup.running.operation_id, 120, 2)

    assert result.state is OperationState.SUCCEEDED
    assert setup.operations.saved == []
    assert setup.clock.sleeps == []


def test_follow_reports_an_unknown_operation_as_not_found() -> None:
    with pytest.raises(OperationNotFoundError):
        make().tracker.follow_operation("missing", 120, 2)


def test_show_returns_the_recorded_operation_without_asking_the_provider() -> None:
    setup = make()

    shown = setup.tracker.show_operation(setup.running.operation_id)

    assert shown.state is OperationState.RUNNING
    assert shown.operation_id == setup.running.operation_id
    assert shown.provider_reference == setup.running.provider_reference
    assert setup.operations.saved == []


def test_show_reports_an_unknown_operation_as_not_found() -> None:
    with pytest.raises(OperationNotFoundError):
        make().tracker.show_operation("missing")


def test_the_operation_summary_has_the_record_fields_and_no_other() -> None:
    setup = make()
    summary = setup.tracker.show_operation(setup.running.operation_id)

    assert {f.name for f in fields(summary)} == {
        "operation_id",
        "configuration_id",
        "server_identifier",
        "action",
        "state",
        "started_at",
        "finished_at",
        "provider_reference",
        "detail",
        "consequence",
    }


def test_follow_reads_a_remove_operation_through_the_removal_port() -> None:
    clock = FakeClock()
    removal = FakeRemovalProvider(steps_to_finish=2, clock=clock)
    store = FakeConfigurationStore()
    store.save(LAB)
    operations = FakeOperationStore()
    running = removal.request_removal(
        LAB, Server("100", "web", "stopped", "", (), False)
    )
    operations.save(running)
    resolver = FakeProviderResolver(FakeProvider(), removal=removal)
    tracker = TrackOperation(
        resolver, store, operations, clock=clock, sleep=clock.sleep
    )

    result = tracker.follow_operation(running.operation_id, 120, 2)

    assert result.state is OperationState.SUCCEEDED
    assert result.action is PowerAction.REMOVE
    assert resolver.removal_resolved == [LAB.configuration_id]
    assert resolver.power_resolved == []
