"""Removal behaviours specific to the Proxmox VE adapter, against the mock API."""

import pytest

from iac_node_forge.application.errors import (
    ProviderTimeoutError,
    ProviderUnreachableError,
    ServerNotFoundError,
)
from iac_node_forge.application.ports import RemovalBlock
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.operation import Operation, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.removal_kind import RemovalKind
from iac_node_forge.domain.server import Server
from iac_node_forge.infrastructure.proxmox import ProxmoxProvider
from tests.contract.conftest import TOKEN, WRONG_TOKEN, configuration_for
from tests.contract.mock_proxmox import MockProxmoxServer

MAX_POLLS = 20


@pytest.fixture
def configuration(mock_proxmox: MockProxmoxServer) -> ProviderConfiguration:
    return configuration_for(mock_proxmox.address)


@pytest.fixture
def provider(mock_proxmox: MockProxmoxServer) -> ProxmoxProvider:
    return ProxmoxProvider(Credentials.single(TOKEN), timeout=2.0)


def server_of(
    provider: ProxmoxProvider, configuration: ProviderConfiguration, vmid: str
) -> Server:
    return provider.get_server(configuration, vmid)


def remove(
    provider: ProxmoxProvider, configuration: ProviderConfiguration, vmid: str
) -> Operation:
    return provider.request_removal(
        configuration, server_of(provider, configuration, vmid)
    )


def test_the_kind_is_destroy(
    provider: ProxmoxProvider, configuration: ProviderConfiguration
) -> None:
    assert provider.removal_kind(configuration) is RemovalKind.DESTROY


def test_a_container_is_deleted_with_purge_and_its_unreferenced_disks(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = remove(provider, configuration, "101")

    assert [(r.method, r.path) for r in mock_proxmox.write_requests] == [
        ("DELETE", "/api2/json/nodes/pve2/lxc/101")
    ]
    assert mock_proxmox.state.deleted == [
        {"purge": "1", "destroy-unreferenced-disks": "1"}
    ]
    assert operation.state is OperationState.RUNNING
    assert operation.action is PowerAction.REMOVE
    assert operation.server_identifier == "101"
    assert (operation.provider_reference or "").startswith("UPID:pve2:")


def test_a_virtual_machine_is_deleted_through_the_qemu_path(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.resources[0]["status"] = "stopped"

    remove(provider, configuration, "100")

    assert [(r.method, r.path) for r in mock_proxmox.write_requests] == [
        ("DELETE", "/api2/json/nodes/pve1/qemu/100")
    ]


def test_the_destroyed_server_is_absent_afterwards_so_reading_it_is_not_found(
    provider: ProxmoxProvider, configuration: ProviderConfiguration
) -> None:
    operation = remove(provider, configuration, "101")
    for _ in range(MAX_POLLS):
        operation = provider.operation_state(configuration, operation)
        if operation.has_ended:
            break

    assert operation.state is OperationState.SUCCEEDED
    with pytest.raises(ServerNotFoundError):
        provider.get_server(configuration, "101")


def test_a_running_server_is_blocked_and_never_stopped_by_the_adapter(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    running = server_of(provider, configuration, "100")

    block = provider.removal_block(configuration, running)
    operation = provider.request_removal(configuration, running)

    assert block is RemovalBlock.RUNNING
    assert operation.state is OperationState.FAILED
    assert [(r.method, r.path) for r in mock_proxmox.write_requests] == [
        ("DELETE", "/api2/json/nodes/pve1/qemu/100")
    ]
    assert mock_proxmox.state.tasks == {}
    assert mock_proxmox.state.resources[0]["status"] == "running"


@pytest.mark.parametrize("state", ["stopped", "paused", "unknown"])
def test_a_server_that_is_not_running_is_not_blocked(
    provider: ProxmoxProvider, configuration: ProviderConfiguration, state: str
) -> None:
    server = Server("101", "db", state, "", (), False)

    assert provider.removal_block(configuration, server) is None


def test_a_server_is_never_reported_as_already_removed(
    provider: ProxmoxProvider, configuration: ProviderConfiguration
) -> None:
    for state in ("running", "stopped", "destroyed", "removed", "cancelled"):
        server = Server("101", "db", state, "", (), False)

        assert provider.removal_block(configuration, server) is not (
            RemovalBlock.ALREADY_REMOVED
        )


def test_the_consequence_says_the_server_and_its_disks_are_destroyed_for_good(
    provider: ProxmoxProvider, configuration: ProviderConfiguration
) -> None:
    server = Server("101", "db", "stopped", "", (), False)

    text = provider.consequence(configuration, server)

    assert "destroys server 101 now" in text
    assert "disks" in text
    assert "cannot be undone" in text


def test_the_reads_before_the_request_send_nothing_at_all(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    server = Server("101", "db", "stopped", "", (), False)

    provider.removal_kind(configuration)
    provider.removal_block(configuration, server)
    provider.consequence(configuration, server)

    assert mock_proxmox.state.requests == []


def test_an_unknown_identifier_is_not_found_and_sends_no_write(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    ghost = Server("999", "ghost", "stopped", "", (), False)

    with pytest.raises(ServerNotFoundError):
        provider.request_removal(configuration, ghost)

    assert mock_proxmox.write_requests == []


def test_a_resource_with_an_unusable_node_gives_failed_and_sends_no_write(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.resources[1]["node"] = "../etc"
    server = Server("101", "db", "stopped", "", (), False)

    operation = provider.request_removal(configuration, server)

    assert operation.state is OperationState.FAILED
    assert mock_proxmox.write_requests == []


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_token_names_the_permission_the_deletion_needs(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
    status: int,
) -> None:
    mock_proxmox.state.delete_forced_status = status

    operation = remove(provider, configuration, "101")

    assert operation.state is OperationState.FAILED
    assert operation.detail is not None
    assert "VM.Allocate" in operation.detail
    assert str(status) in operation.detail
    assert TOKEN not in operation.detail


def test_a_wrong_token_is_rejected_without_leaking_either_token(
    mock_proxmox: MockProxmoxServer, configuration: ProviderConfiguration
) -> None:
    right = ProxmoxProvider(Credentials.single(TOKEN), timeout=2.0)
    wrong = ProxmoxProvider(Credentials.single(WRONG_TOKEN), timeout=2.0)
    server = server_of(right, configuration, "101")

    operation = wrong.request_removal(configuration, server)

    assert operation.state is OperationState.FAILED
    assert operation.detail is not None
    assert TOKEN not in operation.detail
    assert WRONG_TOKEN not in operation.detail
    assert mock_proxmox.state.deleted == []


def test_a_server_error_on_the_delete_is_a_plain_failure(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.delete_forced_status = 500

    operation = remove(provider, configuration, "101")

    assert operation.state is OperationState.FAILED
    assert operation.detail is not None
    assert "500" in operation.detail


def test_a_timeout_on_the_delete_is_unknown_and_names_the_server(
    monkeypatch: pytest.MonkeyPatch,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    def time_out(*args: object, **kwargs: object) -> object:
        raise ProviderTimeoutError("the provider did not answer within 2.0 seconds")

    monkeypatch.setattr(provider, "_delete", time_out)

    operation = remove(provider, configuration, "101")

    assert operation.state is OperationState.UNKNOWN
    assert operation.server_identifier == "101"
    assert operation.detail is not None
    assert "may have reached the provider" in operation.detail


def test_a_refused_connection_on_the_delete_is_a_plain_failure(
    monkeypatch: pytest.MonkeyPatch,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    def refuse(*args: object, **kwargs: object) -> object:
        raise ProviderUnreachableError("the provider could not be reached")

    monkeypatch.setattr(provider, "_delete", refuse)

    operation = remove(provider, configuration, "101")

    assert operation.state is OperationState.FAILED


def test_a_task_that_ends_with_an_error_status_leaves_the_server_in_place(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.task_exitstatus = "unable to destroy CT 101 - busy"
    operation = remove(provider, configuration, "101")
    for _ in range(MAX_POLLS):
        operation = provider.operation_state(configuration, operation)
        if operation.has_ended:
            break

    assert operation.state is OperationState.FAILED
    assert operation.detail == "unable to destroy CT 101 - busy"
    assert server_of(provider, configuration, "101").state == "stopped"
