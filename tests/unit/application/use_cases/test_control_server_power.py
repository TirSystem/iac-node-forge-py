"""UC-008, OC-002: requesting a power action through the fakes."""

from dataclasses import dataclass

import pytest

from iac_node_forge.application.dto import Confirmation, PowerOutcome
from iac_node_forge.application.errors import (
    ConfigurationNotFoundError,
    NoConfigurationError,
    NotAPowerActionError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderUnreachableError,
)
from iac_node_forge.application.use_cases import ControlServerPower
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.operation import OperationState
from iac_node_forge.domain.power_action import POWER_ACTIONS, PowerAction
from iac_node_forge.domain.server import Server
from tests.unit.application.fakes import (
    POWER_CAPABILITY_SET,
    FailingProvider,
    FakeConfigurationStore,
    FakeOperationStore,
    FakePowerProvider,
    FakeProvider,
    FakeProviderResolver,
)
from tests.unit.application.samples import DB, LAB, WEB, WEB_IS_MANAGED

YES = Confirmation(given=True)
NO = Confirmation(given=False)


@dataclass(slots=True)
class Setup:
    use_case: ControlServerPower
    power: FakePowerProvider
    operations: FakeOperationStore


def make(
    servers: tuple[Server, ...] = (WEB, DB),
    capabilities: frozenset[Capability] = POWER_CAPABILITY_SET,
    power: FakePowerProvider | None = None,
    configured: bool = True,
) -> Setup:
    """WEB (100, running) is managed by this client; DB (101, stopped) is not."""
    store = FakeConfigurationStore(managed=(WEB_IS_MANAGED,))
    if configured:
        store.save(LAB)
    fake_power = power or FakePowerProvider()
    resolver = FakeProviderResolver(
        FakeProvider(capabilities=capabilities, servers=servers), power=fake_power
    )
    operations = FakeOperationStore()
    return Setup(
        ControlServerPower(resolver, store, operations), fake_power, operations
    )


def test_request_sends_one_request_and_records_a_running_operation() -> None:
    setup = make()

    result = setup.use_case.request_power_action("Lab", "100", PowerAction.STOP, YES)

    assert result.outcome is PowerOutcome.REQUESTED
    assert result.operation is not None
    assert result.operation.state is OperationState.RUNNING
    assert result.operation.action is PowerAction.STOP
    assert result.operation.server_identifier == "100"
    assert setup.power.requests == [("100", PowerAction.STOP)]
    assert [o.operation_id for o in setup.operations.saved] == [
        result.operation.operation_id
    ]


def test_request_accepts_the_configuration_id_and_a_name_in_any_case() -> None:
    by_name = make().use_case.request_power_action("lab", "100", PowerAction.STOP, YES)
    by_id = make().use_case.request_power_action(
        "cfg-lab", "100", PowerAction.STOP, YES
    )

    assert by_name.outcome is by_id.outcome is PowerOutcome.REQUESTED


def test_request_reports_a_missing_server_as_not_found_without_a_write() -> None:
    setup = make()

    result = setup.use_case.request_power_action("Lab", "999", PowerAction.START, YES)

    assert result.outcome is PowerOutcome.NOT_FOUND
    assert setup.power.requests == []
    assert setup.operations.saved == []


def test_remove_is_refused_as_a_power_action_before_anything_is_read() -> None:
    setup = make()

    with pytest.raises(NotAPowerActionError, match="remove"):
        setup.use_case.request_power_action("Lab", "100", PowerAction.REMOVE, YES)
    with pytest.raises(NotAPowerActionError, match="remove"):
        setup.use_case.preview_power_action("Lab", "100", PowerAction.REMOVE)

    assert setup.power.requests == []
    assert setup.operations.saved == []


def test_request_reports_not_found_when_the_power_port_does_not_know_it() -> None:
    setup = make(power=FakePowerProvider(servers=("101",)))

    result = setup.use_case.request_power_action("Lab", "100", PowerAction.STOP, YES)

    assert result.outcome is PowerOutcome.NOT_FOUND
    assert setup.operations.saved == []


@pytest.mark.parametrize("action", list(POWER_ACTIONS))
def test_request_reports_an_undeclared_capability_as_unsupported(
    action: PowerAction,
) -> None:
    setup = make(capabilities=frozenset({Capability("list_servers")}))

    result = setup.use_case.request_power_action("Lab", "100", action, YES)

    assert result.outcome is PowerOutcome.UNSUPPORTED
    assert action.value in result.reason
    assert setup.power.requests == []
    assert setup.operations.saved == []


def test_request_reports_unsupported_when_the_power_port_refuses_the_action() -> None:
    setup = make(power=FakePowerProvider(supported=frozenset({PowerAction.START})))

    result = setup.use_case.request_power_action("Lab", "100", PowerAction.STOP, YES)

    assert result.outcome is PowerOutcome.UNSUPPORTED
    assert setup.operations.saved == []


@pytest.mark.parametrize(
    ("server", "action"),
    [
        ("100", PowerAction.START),
        ("101", PowerAction.SHUTDOWN),
        ("101", PowerAction.STOP),
    ],
)
def test_request_reports_a_server_already_in_the_state_without_a_record(
    server: str, action: PowerAction
) -> None:
    setup = make()

    result = setup.use_case.request_power_action(
        "Lab", server, action, Confirmation(True, server)
    )

    assert result.outcome is PowerOutcome.ALREADY_IN_STATE
    assert result.server_state in {"running", "stopped"}
    assert setup.power.requests == []
    assert setup.operations.saved == []


def test_already_in_state_is_reported_before_a_confirmation_is_asked_for() -> None:
    result = make().use_case.request_power_action("Lab", "100", PowerAction.START, NO)

    assert result.outcome is PowerOutcome.ALREADY_IN_STATE


def test_restart_of_a_stopped_server_is_not_already_in_state() -> None:
    result = make().use_case.request_power_action(
        "Lab", "101", PowerAction.RESTART, Confirmation(True, "101")
    )

    assert result.outcome is PowerOutcome.REQUESTED


@pytest.mark.parametrize(
    ("server", "confirmation", "outcome"),
    [
        ("100", YES, PowerOutcome.REQUESTED),
        ("100", NO, PowerOutcome.NOT_CONFIRMED),
        ("100", Confirmation(True, "wrong"), PowerOutcome.REQUESTED),
        ("100", Confirmation(False, "100"), PowerOutcome.NOT_CONFIRMED),
        ("101", YES, PowerOutcome.NOT_CONFIRMED),
        ("101", Confirmation(True, "101"), PowerOutcome.REQUESTED),
        ("101", Confirmation(True, "100"), PowerOutcome.IDENTIFIER_MISMATCH),
        ("101", Confirmation(True, "101 "), PowerOutcome.IDENTIFIER_MISMATCH),
        ("101", Confirmation(False, "101"), PowerOutcome.NOT_CONFIRMED),
    ],
)
def test_confirmation_matrix_sends_only_when_the_rules_are_met(
    server: str, confirmation: Confirmation, outcome: PowerOutcome
) -> None:
    """The server 100 is managed by this client, 101 is not; both accept RESTART."""
    setup = make()

    result = setup.use_case.request_power_action(
        "Lab", server, PowerAction.RESTART, confirmation
    )

    assert result.outcome is outcome
    sent = outcome is PowerOutcome.REQUESTED
    assert setup.power.requests == ([(server, PowerAction.RESTART)] if sent else [])
    assert len(setup.operations.saved) == (1 if sent else 0)


def test_result_says_whether_the_server_is_managed() -> None:
    setup = make()

    managed = setup.use_case.preview_power_action("Lab", "100", PowerAction.STOP)
    unmanaged = setup.use_case.preview_power_action("Lab", "101", PowerAction.START)

    assert managed.managed is True
    assert unmanaged.managed is False


def test_preview_sends_and_records_nothing() -> None:
    setup = make()

    ready = setup.use_case.preview_power_action("Lab", "100", PowerAction.STOP)
    already = setup.use_case.preview_power_action("Lab", "100", PowerAction.START)
    missing = setup.use_case.preview_power_action("Lab", "999", PowerAction.START)

    assert ready.outcome is PowerOutcome.READY
    assert ready.server_name == "web"
    assert ready.server_state == "running"
    assert already.outcome is PowerOutcome.ALREADY_IN_STATE
    assert missing.outcome is PowerOutcome.NOT_FOUND
    assert setup.power.requests == []
    assert setup.operations.saved == []


@pytest.mark.parametrize(
    "error",
    [
        ProviderAuthenticationError("the provider answered 403"),
        ProviderRateLimitError("too many requests"),
        ProviderUnreachableError("timed out"),
    ],
)
def test_request_records_a_failed_operation_when_the_provider_rejects_it(
    error: ProviderAuthenticationError,
) -> None:
    setup = make()
    setup.power.reject_with(error)

    result = setup.use_case.request_power_action("Lab", "100", PowerAction.STOP, YES)

    assert result.outcome is PowerOutcome.REJECTED
    assert result.operation is not None
    assert result.operation.state is OperationState.FAILED
    assert result.operation.detail == str(error)
    assert result.reason == str(error)
    assert result.operation.finished_at is not None
    assert len(setup.operations.saved) == 1
    assert setup.power.requests == []


def test_request_fails_for_an_unknown_configuration_without_a_write() -> None:
    setup = make()

    with pytest.raises(ConfigurationNotFoundError):
        setup.use_case.request_power_action("Nope", "100", PowerAction.STOP, YES)

    assert setup.power.requests == []


def test_request_fails_when_nothing_is_configured() -> None:
    setup = make(configured=False)

    with pytest.raises(NoConfigurationError):
        setup.use_case.request_power_action("Lab", "100", PowerAction.STOP, YES)


def test_a_provider_that_cannot_be_read_raises_and_nothing_is_written() -> None:
    store = FakeConfigurationStore()
    store.save(LAB)
    power = FakePowerProvider()
    resolver = FakeProviderResolver(
        FailingProvider(ProviderUnreachableError("timed out")), power=power
    )
    operations = FakeOperationStore()
    use_case = ControlServerPower(resolver, store, operations)

    with pytest.raises(ProviderUnreachableError):
        use_case.request_power_action("Lab", "100", PowerAction.STOP, YES)

    assert power.requests == []
    assert operations.saved == []


def test_request_resolves_the_power_port_only_after_the_confirmation() -> None:
    store = FakeConfigurationStore()
    store.save(LAB)
    resolver = FakeProviderResolver(
        FakeProvider(capabilities=POWER_CAPABILITY_SET, servers=(DB,)),
        power=FakePowerProvider(),
    )
    use_case = ControlServerPower(resolver, store, FakeOperationStore())

    use_case.request_power_action("Lab", "101", PowerAction.START, NO)

    assert resolver.power_resolved == []


def test_provision_is_refused_as_a_power_action_before_anything_is_read() -> None:
    setup = make()

    with pytest.raises(NotAPowerActionError, match="provision"):
        setup.use_case.request_power_action("Lab", "100", PowerAction.PROVISION, YES)
    with pytest.raises(NotAPowerActionError, match="provision"):
        setup.use_case.preview_power_action("Lab", "100", PowerAction.PROVISION)

    assert setup.power.requests == []
    assert setup.operations.saved == []


@pytest.mark.parametrize("action", [PowerAction.BACKUP, PowerAction.RESTORE])
def test_backup_and_restore_are_refused_as_power_actions_before_anything_is_read(
    action: PowerAction,
) -> None:
    setup = make()

    with pytest.raises(NotAPowerActionError, match=action.value):
        setup.use_case.request_power_action("Lab", "100", action, YES)
    with pytest.raises(NotAPowerActionError, match=action.value):
        setup.use_case.preview_power_action("Lab", "100", action)

    assert setup.power.requests == []
    assert setup.operations.saved == []
