"""The capability mapping and the power fakes against their ports."""

import pytest

from iac_node_forge.application import capabilities
from iac_node_forge.application.errors import (
    OperationNotFoundError,
    PowerActionUnsupportedError,
    ProviderAuthenticationError,
    ServerNotFoundError,
    UnsupportedProviderError,
)
from iac_node_forge.application.ports import (
    OperationStore,
    ProviderResolver,
    ServerPowerPort,
)
from iac_node_forge.domain.operation import OperationId, OperationState
from iac_node_forge.domain.power_action import POWER_ACTIONS, PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from tests.unit.application.fakes import (
    FakeOperationStore,
    FakePowerProvider,
    FakeProvider,
    FakeProviderResolver,
)

CONFIGURATION = ProviderConfiguration(
    ConfigurationId("cfg-1"), ProviderKind.PROXMOX_VE, "Lab", "pve.example:8006"
)


def test_every_power_action_maps_to_one_capability_name() -> None:
    assert set(capabilities.POWER_ACTION_CAPABILITY) == set(POWER_ACTIONS)
    assert capabilities.POWER_ACTION_CAPABILITY[PowerAction.START] == "start_server"
    assert (
        capabilities.POWER_ACTION_CAPABILITY[PowerAction.SHUTDOWN] == "shutdown_server"
    )
    assert capabilities.POWER_ACTION_CAPABILITY[PowerAction.STOP] == "stop_server"
    assert capabilities.POWER_ACTION_CAPABILITY[PowerAction.RESTART] == "restart_server"


def test_remove_is_not_a_power_action_but_has_its_own_capability() -> None:
    assert PowerAction.REMOVE not in POWER_ACTIONS
    assert PowerAction.REMOVE not in capabilities.POWER_ACTION_CAPABILITY
    assert capabilities.ACTION_CAPABILITY[PowerAction.REMOVE] == "remove_server"
    assert capabilities.REMOVE_SERVER not in capabilities.POWER_CAPABILITIES
    assert capabilities.REMOVE_SERVER in capabilities.KNOWN_ACTIONS


def test_provision_is_not_a_power_action_but_has_its_own_capability() -> None:
    assert PowerAction.PROVISION not in POWER_ACTIONS
    assert PowerAction.PROVISION not in capabilities.POWER_ACTION_CAPABILITY
    assert set(capabilities.ACTION_CAPABILITY) == set(PowerAction)
    assert capabilities.ACTION_CAPABILITY[PowerAction.PROVISION] == "provision_server"
    assert (
        capabilities.ACTION_CAPABILITY[PowerAction.START]
        == capabilities.POWER_ACTION_CAPABILITY[PowerAction.START]
    )
    assert capabilities.PROVISION_SERVER not in capabilities.POWER_CAPABILITIES


def test_power_capability_names_are_distinct_and_known() -> None:
    names = list(capabilities.POWER_ACTION_CAPABILITY.values())

    assert len(set(names)) == len(names)
    assert set(names) <= set(capabilities.KNOWN_ACTIONS)
    assert capabilities.POWER_CAPABILITIES == frozenset(names)


def test_fake_power_provider_satisfies_server_power_port() -> None:
    port: ServerPowerPort = FakePowerProvider()
    assert isinstance(port, ServerPowerPort)


def test_fake_operation_store_satisfies_operation_store() -> None:
    port: OperationStore = FakeOperationStore()
    assert isinstance(port, OperationStore)


def test_fake_resolver_satisfies_provider_resolver() -> None:
    port: ProviderResolver = FakeProviderResolver(FakeProvider())
    assert isinstance(port, ProviderResolver)


def test_request_returns_running_operation_and_records_the_request() -> None:
    power = FakePowerProvider()

    operation = power.request_power(CONFIGURATION, "100", PowerAction.START)

    assert operation.state is OperationState.RUNNING
    assert operation.action is PowerAction.START
    assert operation.configuration_id == "cfg-1"
    assert power.requests == [("100", PowerAction.START)]


def test_operation_ends_after_the_configured_number_of_steps() -> None:
    power = FakePowerProvider(steps_to_finish=3)
    operation = power.request_power(CONFIGURATION, "100", PowerAction.STOP)

    first = power.operation_state(CONFIGURATION, operation)
    second = power.operation_state(CONFIGURATION, first)
    third = power.operation_state(CONFIGURATION, second)

    assert first.state is OperationState.RUNNING
    assert second.state is OperationState.RUNNING
    assert third.state is OperationState.SUCCEEDED
    assert third.finished_at is not None


def test_failing_operations_end_failed_with_a_detail() -> None:
    power = FakePowerProvider(steps_to_finish=1)
    power.make_operations_fail()
    operation = power.request_power(CONFIGURATION, "100", PowerAction.STOP)

    result = power.operation_state(CONFIGURATION, operation)

    assert result.state is OperationState.FAILED
    assert result.detail


def test_rejected_request_gives_a_failed_operation_and_no_recorded_request() -> None:
    power = FakePowerProvider()
    power.reject_with(ProviderAuthenticationError("answered 403"))

    operation = power.request_power(CONFIGURATION, "100", PowerAction.START)

    assert operation.state is OperationState.FAILED
    assert operation.detail == "answered 403"
    assert power.requests == []


def test_unsupported_action_raises_and_sends_nothing() -> None:
    power = FakePowerProvider(supported=frozenset({PowerAction.START}))

    with pytest.raises(PowerActionUnsupportedError):
        power.request_power(CONFIGURATION, "100", PowerAction.RESTART)

    assert power.requests == []


def test_unknown_server_raises_not_found() -> None:
    with pytest.raises(ServerNotFoundError):
        FakePowerProvider().request_power(CONFIGURATION, "999", PowerAction.START)


def test_store_returns_what_was_saved() -> None:
    power = FakePowerProvider()
    store = FakeOperationStore()
    operation = power.request_power(CONFIGURATION, "100", PowerAction.START)

    store.save(operation)

    assert store.load(operation.operation_id) == operation


def test_store_raises_not_found_for_unknown_operation() -> None:
    with pytest.raises(OperationNotFoundError):
        FakeOperationStore().load(OperationId("missing"))


def test_fake_resolver_gives_the_power_port_of_a_configuration() -> None:
    power = FakePowerProvider()
    resolver = FakeProviderResolver(FakeProvider(), power=power)

    assert resolver.power_for(CONFIGURATION) is power
    assert resolver.power_resolved == [CONFIGURATION.configuration_id]


def test_fake_resolver_raises_when_no_power_port_exists() -> None:
    resolver = FakeProviderResolver(FakeProvider())

    with pytest.raises(UnsupportedProviderError):
        resolver.power_for(CONFIGURATION)
