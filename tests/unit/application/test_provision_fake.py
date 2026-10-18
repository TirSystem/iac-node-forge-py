"""The provision fake against its port, and the resolver's provision_for."""

import pytest

from iac_node_forge.application.errors import (
    ProviderAuthenticationError,
    ProvisionUnsupportedError,
    UnsupportedProviderError,
)
from iac_node_forge.application.ports import ProviderResolver, ServerProvisionPort
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.operation import OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from tests.unit.application.fakes import FakeProvider, FakeProviderResolver
from tests.unit.application.fakes import FakeProvisionProvider as Fake

CONFIGURATION = ProviderConfiguration(
    ConfigurationId("cfg-1"), ProviderKind.PROXMOX_VE, "Lab", "pve.example:8006"
)
DESIRED = DesiredConfiguration(ConfigurationId("cfg-1"), "web", "alpine")


def test_fake_satisfies_the_provision_port() -> None:
    port: ServerProvisionPort = Fake()
    assert isinstance(port, ServerProvisionPort)


def test_fake_declares_the_provision_capability_only_when_supported() -> None:
    supported = {c.name for c in Fake().discover_capabilities(CONFIGURATION)}
    unsupported = {
        c.name for c in Fake(supported=False).discover_capabilities(CONFIGURATION)
    }

    assert "provision_server" in supported
    assert "provision_server" not in unsupported


def test_request_without_the_capability_is_refused_and_records_nothing() -> None:
    fake = Fake(supported=False)

    with pytest.raises(ProvisionUnsupportedError):
        fake.request_provision(CONFIGURATION, DESIRED, "web-1", None)

    assert fake.requests == []


def test_request_returns_a_running_provision_operation_that_names_the_server() -> None:
    fake = Fake()

    operation = fake.request_provision(CONFIGURATION, DESIRED, "web-1", "150")

    assert operation.action is PowerAction.PROVISION
    assert operation.state is OperationState.RUNNING
    assert operation.server_identifier == "150"
    assert fake.requests == [("web-1", "150")]


def test_a_provider_that_names_the_server_itself_gives_no_reserved_identifier() -> None:
    fake = Fake(reserves=False)

    assert fake.reserve_identifier(CONFIGURATION) is None
    operation = fake.request_provision(CONFIGURATION, DESIRED, "web-1", None)

    assert operation.server_identifier is not None


def test_following_the_operation_adds_the_server_to_the_listing() -> None:
    fake = Fake(steps_to_finish=2)
    operation = fake.request_provision(CONFIGURATION, DESIRED, "web-1", None)

    running = fake.operation_state(CONFIGURATION, operation)
    assert running.state is OperationState.RUNNING
    assert fake.list_servers(CONFIGURATION) == ()
    ended = fake.operation_state(CONFIGURATION, running)

    assert ended.state is OperationState.SUCCEEDED
    (created,) = fake.list_servers(CONFIGURATION)
    assert created.name == "web-1"
    assert created.server_identifier == operation.server_identifier


def test_a_scripted_rejection_gives_a_failed_operation_with_no_server() -> None:
    fake = Fake()
    fake.reject_with(ProviderAuthenticationError("the fake answered 403"))

    operation = fake.request_provision(CONFIGURATION, DESIRED, "web-1", None)

    assert operation.state is OperationState.FAILED
    assert operation.server_identifier is None
    assert operation.detail == "the fake answered 403"
    assert fake.requests == []


def test_the_reads_are_recorded_and_send_no_request() -> None:
    fake = Fake(cost="commits the account")

    fake.value_rules(CONFIGURATION)
    fake.reserve_identifier(CONFIGURATION)
    cost = fake.cost_consequence(CONFIGURATION, DESIRED)

    assert cost == "commits the account"
    assert fake.requests == []
    assert fake.reads == ["value_rules", "reserve_identifier", "cost_consequence"]


def test_resolver_gives_the_provision_port_of_a_provider_that_has_one() -> None:
    fake = Fake()
    resolver: ProviderResolver = FakeProviderResolver(fake)

    assert resolver.provision_for(CONFIGURATION) is fake


def test_resolver_refuses_a_provider_without_a_provision_port() -> None:
    resolver = FakeProviderResolver(FakeProvider())

    with pytest.raises(UnsupportedProviderError):
        resolver.provision_for(CONFIGURATION)
