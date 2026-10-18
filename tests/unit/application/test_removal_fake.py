"""The removal fake against its port, and the resolver's removal_for."""

import pytest

from iac_node_forge.application.errors import (
    ProviderAuthenticationError,
    RemovalUnsupportedError,
    UnsupportedProviderError,
)
from iac_node_forge.application.ports import (
    ProviderResolver,
    RemovalBlock,
    ServerRemovalPort,
)
from iac_node_forge.domain.operation import OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.removal_kind import RemovalKind
from iac_node_forge.domain.server import Server
from tests.unit.application.fakes import FakeProvider, FakeProviderResolver
from tests.unit.application.fakes import FakeRemovalProvider as Fake

CONFIGURATION = ProviderConfiguration(
    ConfigurationId("cfg-1"), ProviderKind.PROXMOX_VE, "Lab", "pve.example:8006"
)
RUNNING = Server("100", "web", "running", "small", (), False)
STOPPED = Server("101", "db", "stopped", "small", (), False)
CANCELLED = Server("102", "old", "cancelled", "small", (), False)


def test_fake_satisfies_the_removal_port() -> None:
    port: ServerRemovalPort = Fake()
    assert isinstance(port, ServerRemovalPort)


def test_fake_declares_the_remove_capability_only_when_supported() -> None:
    supported = {c.name for c in Fake().discover_capabilities(CONFIGURATION)}
    unsupported = {
        c.name for c in Fake(supported=False).discover_capabilities(CONFIGURATION)
    }

    assert "remove_server" in supported
    assert "remove_server" not in unsupported


def test_request_without_the_capability_is_refused_and_records_nothing() -> None:
    fake = Fake(servers=(STOPPED,), supported=False)

    with pytest.raises(RemovalUnsupportedError):
        fake.request_removal(CONFIGURATION, STOPPED)

    assert fake.requests == []


def test_destroy_blocks_a_running_server_only() -> None:
    fake = Fake(kind=RemovalKind.DESTROY)

    assert fake.removal_block(CONFIGURATION, RUNNING) is RemovalBlock.RUNNING
    assert fake.removal_block(CONFIGURATION, STOPPED) is None
    assert fake.removal_block(CONFIGURATION, CANCELLED) is None


def test_cancel_blocks_a_cancelled_server_only() -> None:
    fake = Fake(kind=RemovalKind.CANCEL)

    assert fake.removal_block(CONFIGURATION, CANCELLED) is RemovalBlock.ALREADY_REMOVED
    assert fake.removal_block(CONFIGURATION, RUNNING) is None
    assert fake.removal_block(CONFIGURATION, STOPPED) is None


def test_request_returns_a_running_remove_operation_for_the_server() -> None:
    fake = Fake(servers=(STOPPED,))

    operation = fake.request_removal(CONFIGURATION, STOPPED)

    assert operation.action is PowerAction.REMOVE
    assert operation.state is OperationState.RUNNING
    assert operation.server_identifier == "101"
    assert fake.requests == ["101"]


def test_following_a_destroy_takes_the_server_out_of_the_listing() -> None:
    fake = Fake(servers=(RUNNING, STOPPED), steps_to_finish=2)
    operation = fake.request_removal(CONFIGURATION, STOPPED)

    running = fake.operation_state(CONFIGURATION, operation)
    assert running.state is OperationState.RUNNING
    assert fake.list_servers(CONFIGURATION) == (RUNNING, STOPPED)
    ended = fake.operation_state(CONFIGURATION, running)

    assert ended.state is OperationState.SUCCEEDED
    assert ended.detail is None
    assert fake.list_servers(CONFIGURATION) == (RUNNING,)


def test_following_a_cancel_leaves_the_server_listed_as_cancelled_with_a_date() -> None:
    fake = Fake(servers=(STOPPED,), kind=RemovalKind.CANCEL, steps_to_finish=1)
    operation = fake.request_removal(CONFIGURATION, STOPPED)

    ended = fake.operation_state(CONFIGURATION, operation)

    assert ended.state is OperationState.SUCCEEDED
    assert ended.detail == "cancelled, effective 2026-11-18"
    (left,) = fake.list_servers(CONFIGURATION)
    assert left.state == "cancelled"


def test_a_cancel_without_a_reported_date_says_so() -> None:
    fake = Fake(
        servers=(STOPPED,),
        kind=RemovalKind.CANCEL,
        steps_to_finish=1,
        effective_date=None,
    )

    ended = fake.operation_state(
        CONFIGURATION, fake.request_removal(CONFIGURATION, STOPPED)
    )

    assert ended.state is OperationState.SUCCEEDED
    assert ended.detail == "cancelled, no date was reported"


def test_a_scripted_rejection_gives_a_failed_operation_and_records_nothing() -> None:
    fake = Fake(servers=(STOPPED,))
    fake.reject_with(ProviderAuthenticationError("the fake answered 403"))

    operation = fake.request_removal(CONFIGURATION, STOPPED)

    assert operation.state is OperationState.FAILED
    assert operation.server_identifier == "101"
    assert operation.detail == "the fake answered 403"
    assert fake.requests == []


def test_a_lost_answer_gives_unknown_and_the_server_stays_listed() -> None:
    fake = Fake(servers=(STOPPED,))
    fake.answer_unknown()

    operation = fake.request_removal(CONFIGURATION, STOPPED)

    assert operation.state is OperationState.UNKNOWN
    assert fake.list_servers(CONFIGURATION) == (STOPPED,)


def test_the_reads_are_recorded_and_send_no_request() -> None:
    fake = Fake(servers=(STOPPED,))

    fake.removal_kind(CONFIGURATION)
    fake.removal_block(CONFIGURATION, STOPPED)
    text = fake.consequence(CONFIGURATION, STOPPED)

    assert "101" in text
    assert fake.requests == []
    assert fake.reads == ["removal_kind", "removal_block", "consequence"]


def test_resolver_gives_the_removal_port_of_a_provider_that_has_one() -> None:
    fake = Fake()
    resolver: ProviderResolver = FakeProviderResolver(fake)

    assert resolver.removal_for(CONFIGURATION) is fake


def test_resolver_refuses_a_provider_without_a_removal_port() -> None:
    resolver = FakeProviderResolver(FakeProvider())

    with pytest.raises(UnsupportedProviderError):
        resolver.removal_for(CONFIGURATION)
