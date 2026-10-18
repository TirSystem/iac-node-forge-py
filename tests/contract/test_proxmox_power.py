"""Power behaviours specific to the Proxmox VE adapter, against the mock API."""

import inspect

import pytest

from iac_node_forge.application.errors import ProviderError, ServerNotFoundError
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.operation import (
    MAX_DETAIL_LENGTH,
    Operation,
    OperationId,
    OperationState,
)
from iac_node_forge.domain.power_action import POWER_ACTIONS, PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.infrastructure.proxmox import ProxmoxProvider
from iac_node_forge.infrastructure.proxmox import provider as proxmox_module
from tests.contract.conftest import TOKEN, WRONG_TOKEN, configuration_for
from tests.contract.mock_proxmox import POWER_VERBS, MockProxmoxServer

TOKEN_SECRET = TOKEN.split("=", 1)[1]


@pytest.fixture
def configuration(mock_proxmox: MockProxmoxServer) -> ProviderConfiguration:
    return configuration_for(mock_proxmox.address)


@pytest.mark.parametrize(
    ("action", "verb"),
    [
        (PowerAction.START, "start"),
        (PowerAction.SHUTDOWN, "shutdown"),
        (PowerAction.STOP, "stop"),
        (PowerAction.RESTART, "reboot"),
    ],
)
def test_action_posts_to_the_status_path_of_the_vm_on_its_node(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
    action: PowerAction,
    verb: str,
) -> None:
    proxmox_provider.request_power(configuration, "100", action)

    posts = [(r.method, r.path) for r in mock_proxmox.write_requests]
    assert posts == [("POST", f"/api2/json/nodes/pve1/qemu/100/status/{verb}")]
    assert verb in POWER_VERBS


def test_container_is_addressed_by_its_own_node_and_type(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    proxmox_provider.request_power(configuration, "101", PowerAction.START)

    assert [r.path for r in mock_proxmox.write_requests] == [
        "/api2/json/nodes/pve2/lxc/101/status/start"
    ]


def test_task_identifier_is_stored_as_provider_reference(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = proxmox_provider.request_power(configuration, "100", PowerAction.STOP)

    assert operation.provider_reference in mock_proxmox.state.tasks
    assert (operation.provider_reference or "").startswith("UPID:pve1:")


def test_task_status_is_read_from_the_node_of_the_task(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.task_polls_before_end = 0
    operation = proxmox_provider.request_power(configuration, "100", PowerAction.STOP)

    result = proxmox_provider.operation_state(configuration, operation)

    assert result.state is OperationState.SUCCEEDED
    last = mock_proxmox.state.requests[-1]
    assert last.method == "GET"
    assert last.path.startswith("/api2/json/nodes/pve1/tasks/UPID:pve1:")
    assert last.path.endswith("/status")


def test_running_task_leaves_the_operation_running(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.task_polls_before_end = 5
    operation = proxmox_provider.request_power(configuration, "100", PowerAction.STOP)

    result = proxmox_provider.operation_state(configuration, operation)

    assert result == operation


def test_other_exit_status_gives_failed_with_the_exit_status_as_detail(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.task_polls_before_end = 0
    mock_proxmox.state.task_exitstatus = "VM quit/powerdown failed - got timeout"
    operation = proxmox_provider.request_power(
        configuration, "100", PowerAction.SHUTDOWN
    )

    result = proxmox_provider.operation_state(configuration, operation)

    assert result.state is OperationState.FAILED
    assert result.detail == "VM quit/powerdown failed - got timeout"
    assert result.finished_at is not None


def test_long_exit_status_is_cut_to_the_detail_limit(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.task_polls_before_end = 0
    mock_proxmox.state.task_exitstatus = "error " * 400
    operation = proxmox_provider.request_power(configuration, "100", PowerAction.STOP)

    result = proxmox_provider.operation_state(configuration, operation)

    assert result.state is OperationState.FAILED
    assert result.detail is not None
    assert len(result.detail) == MAX_DETAIL_LENGTH
    assert result.detail.endswith("...")


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_failure_names_the_permission_and_no_secret(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
    status: int,
) -> None:
    mock_proxmox.state.power_forced_status = status

    operation = proxmox_provider.request_power(configuration, "100", PowerAction.STOP)

    assert operation.state is OperationState.FAILED
    assert operation.detail is not None
    assert str(status) in operation.detail
    assert "VM.PowerMgmt" in operation.detail
    assert TOKEN not in operation.detail
    assert TOKEN_SECRET not in operation.detail


def test_wrong_token_gives_failed_operation_without_any_task(
    mock_proxmox: MockProxmoxServer, configuration: ProviderConfiguration
) -> None:
    provider = ProxmoxProvider(Credentials.single(WRONG_TOKEN), timeout=2.0)

    operation = provider.request_power(configuration, "100", PowerAction.STOP)

    assert operation.state is OperationState.FAILED
    assert mock_proxmox.write_requests == []
    assert mock_proxmox.state.tasks == {}


@pytest.mark.parametrize("status", [429, 500, 502])
def test_server_side_failure_gives_failed_operation_with_the_status(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
    status: int,
) -> None:
    mock_proxmox.state.power_forced_status = status

    operation = proxmox_provider.request_power(configuration, "100", PowerAction.STOP)

    assert operation.state is OperationState.FAILED
    assert operation.detail is not None
    assert str(status) in operation.detail
    assert len(mock_proxmox.write_requests) == 1


def test_forced_failure_on_the_lookup_gives_failed_and_sends_no_post(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.forced_status = 500

    operation = proxmox_provider.request_power(configuration, "100", PowerAction.STOP)

    assert operation.state is OperationState.FAILED
    assert mock_proxmox.write_requests == []


def test_unreachable_provider_gives_failed_operation(
    proxmox_provider: ProxmoxProvider,
) -> None:
    configuration = configuration_for("http://127.0.0.1:1")

    operation = proxmox_provider.request_power(configuration, "100", PowerAction.STOP)

    assert operation.state is OperationState.FAILED
    assert operation.detail


def test_unknown_vmid_raises_not_found_and_sends_no_post(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    with pytest.raises(ServerNotFoundError):
        proxmox_provider.request_power(configuration, "999", PowerAction.STOP)

    assert mock_proxmox.write_requests == []


def test_resource_with_unusable_node_gives_failed_operation(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.resources[0]["node"] = "../etc"

    operation = proxmox_provider.request_power(configuration, "100", PowerAction.STOP)

    assert operation.state is OperationState.FAILED
    assert mock_proxmox.write_requests == []


def test_only_get_and_the_power_posts_are_ever_sent(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.task_polls_before_end = 0
    for action in POWER_ACTIONS:
        operation = proxmox_provider.request_power(configuration, "100", action)
        proxmox_provider.operation_state(configuration, operation)

    assert mock_proxmox.methods_used == {"GET", "POST"}
    assert {r.path.rsplit("/", 1)[1] for r in mock_proxmox.write_requests} == (
        POWER_VERBS
    )


def test_operation_without_task_identifier_is_not_readable(
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = proxmox_provider.request_power(configuration, "100", PowerAction.STOP)
    stray = Operation(
        operation_id=OperationId("x"),
        configuration_id=operation.configuration_id,
        server_identifier="100",
        action=PowerAction.STOP,
        state=OperationState.RUNNING,
        started_at=operation.started_at,
    )

    with pytest.raises(ProviderError):
        proxmox_provider.operation_state(configuration, stray)


def test_task_the_provider_does_not_know_raises_provider_error(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = proxmox_provider.request_power(configuration, "100", PowerAction.STOP)
    mock_proxmox.state.tasks.clear()

    with pytest.raises(ProviderError):
        proxmox_provider.operation_state(configuration, operation)


def test_power_capabilities_are_declared(
    proxmox_provider: ProxmoxProvider, configuration: ProviderConfiguration
) -> None:
    names = {c.name for c in proxmox_provider.discover_capabilities(configuration)}

    assert {
        "start_server",
        "shutdown_server",
        "stop_server",
        "restart_server",
    } <= names


def test_adapter_source_names_no_write_method_beyond_power_and_creation() -> None:
    source = inspect.getsource(proxmox_module)

    assert source.count('"POST"') == 1
    # The power request and the creation, the only callers of ``_post``.
    assert source.count("self._post(") == 2
    assert source.count("_POWER_VERB[") == 1
    for write_call in (".put(", ".patch(", ".delete(", ".post("):
        assert write_call not in source
    assert len(proxmox_module._POWER_VERB) == 4
