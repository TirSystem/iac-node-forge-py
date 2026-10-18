"""Provisioning behaviours specific to the Proxmox VE adapter, against the mock API."""

from dataclasses import replace

import pytest

from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.operation import OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.provider_value import ProviderValue
from iac_node_forge.domain.value_rule import IMAGE_RULE_NAMES
from iac_node_forge.infrastructure.proxmox import ProxmoxProvider
from iac_node_forge.infrastructure.proxmox._provision import value_rules
from tests.contract.conftest import (
    EXPECTED_TEMPLATES,
    PROXMOX_DESIRED,
    SERVER_NAME,
    TOKEN,
    WRONG_TOKEN,
    configuration_for,
)
from tests.contract.mock_proxmox import MockProxmoxServer

NET_LXC = "name=eth0,bridge=vmbr0,ip=dhcp"


@pytest.fixture
def configuration(mock_proxmox: MockProxmoxServer) -> ProviderConfiguration:
    return configuration_for(mock_proxmox.address)


@pytest.fixture
def provider(mock_proxmox: MockProxmoxServer) -> ProxmoxProvider:
    return ProxmoxProvider(Credentials.single(TOKEN), timeout=2.0)


def with_values(desired: DesiredConfiguration, **changes: str) -> DesiredConfiguration:
    return replace(
        desired,
        values=tuple(
            ProviderValue(v.name, changes.get(v.name, v.value)) for v in desired.values
        ),
    )


def test_container_creation_posts_the_lxc_fields_and_returns_the_vmid(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = provider.request_provision(
        configuration, PROXMOX_DESIRED, SERVER_NAME, "102"
    )

    assert mock_proxmox.state.created == [
        {
            "vmid": "102",
            "hostname": SERVER_NAME,
            "ostemplate": "local:vztmpl/alpine-3.24-default.tar.xz",
            "rootfs": "local-lvm:4",
            "memory": "512",
            "cores": "1",
            "unprivileged": "1",
            "net0": NET_LXC,
            "start": "0",
        }
    ]
    assert [(r.method, r.path) for r in mock_proxmox.write_requests] == [
        ("POST", "/api2/json/nodes/pve1/lxc")
    ]
    assert operation.state is OperationState.RUNNING
    assert operation.action is PowerAction.PROVISION
    assert operation.server_identifier == "102"
    assert (operation.provider_reference or "").startswith("UPID:pve1:")


def test_virtual_machine_creation_posts_the_qemu_fields(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    desired = replace(
        with_values(PROXMOX_DESIRED, kind="qemu", storage="pve1/local-lvm"),
        image="pve1/local:iso/debian-12.iso",
    )

    provider.request_provision(configuration, desired, SERVER_NAME, "103")

    assert mock_proxmox.state.created == [
        {
            "vmid": "103",
            "name": SERVER_NAME,
            "memory": "512",
            "cores": "1",
            "scsi0": "local-lvm:4",
            "ide2": "local:iso/debian-12.iso,media=cdrom",
            "net0": "virtio,bridge=vmbr0",
            "start": "0",
        }
    ]
    assert [r.path for r in mock_proxmox.write_requests] == [
        "/api2/json/nodes/pve1/qemu"
    ]


def test_the_next_free_identifier_follows_the_highest_vmid(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    assert provider.reserve_identifier(configuration) == "102"
    assert [r.path for r in mock_proxmox.state.requests] == [
        "/api2/json/cluster/nextid"
    ]


def test_without_a_reserved_identifier_the_next_free_one_is_read_and_used(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = provider.request_provision(
        configuration, PROXMOX_DESIRED, SERVER_NAME, None
    )

    assert operation.server_identifier == "102"
    assert mock_proxmox.state.created[0]["vmid"] == "102"


def test_no_commitment_is_stated(
    provider: ProxmoxProvider, configuration: ProviderConfiguration
) -> None:
    assert provider.cost_consequence(configuration, PROXMOX_DESIRED) is None


def test_the_value_rules_check_kind_options_and_integer_bounds(
    provider: ProxmoxProvider, configuration: ProviderConfiguration
) -> None:
    rules = {r.name: r for r in provider.value_rules(configuration)}

    assert rules["kind"].allowed == ("lxc", "qemu")
    assert rules["node"].option_kind == "node"
    assert rules["storage"].option_kind == "storage"
    assert rules["template"].option_kind == "template"
    for name in ("memory_mb", "cores", "disk_gb"):
        assert rules[name].option_kind is None
        assert rules[name].minimum is not None
        assert rules[name].maximum is not None
        assert rules[name].minimum < rules[name].maximum  # type: ignore[operator]


def test_templates_come_from_the_vztmpl_and_iso_content_of_storages_that_hold_them(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    options = provider.list_server_options(configuration, "template")

    assert options == EXPECTED_TEMPLATES
    paths = {r.path for r in mock_proxmox.state.requests}
    assert "/api2/json/nodes/pve1/storage/local/content" in paths
    assert "/api2/json/nodes/pve1/storage/local-lvm/content" not in paths
    assert {r.method for r in mock_proxmox.state.requests} == {"GET"}


def test_a_taken_identifier_gives_a_failed_operation_without_a_server(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = provider.request_provision(
        configuration, PROXMOX_DESIRED, SERVER_NAME, "100"
    )

    assert operation.state is OperationState.FAILED
    assert operation.server_identifier is None
    assert operation.detail is not None
    assert "status 500" in operation.detail
    assert mock_proxmox.state.tasks == {}


@pytest.mark.parametrize("status", [401, 403])
def test_a_permission_refusal_names_vm_allocate_and_no_secret(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
    status: int,
) -> None:
    mock_proxmox.state.create_forced_status = status

    operation = provider.request_provision(
        configuration, PROXMOX_DESIRED, SERVER_NAME, "102"
    )

    assert operation.state is OperationState.FAILED
    assert operation.detail is not None
    assert "VM.Allocate" in operation.detail
    assert "VM.PowerMgmt" not in operation.detail
    assert TOKEN not in operation.detail


def test_a_wrong_token_gives_a_failed_operation_that_leaks_neither_token(
    mock_proxmox: MockProxmoxServer, configuration: ProviderConfiguration
) -> None:
    wrong = ProxmoxProvider(Credentials.single(WRONG_TOKEN), timeout=2.0)

    operation = wrong.request_provision(
        configuration, PROXMOX_DESIRED, SERVER_NAME, "102"
    )

    assert operation.state is OperationState.FAILED
    assert operation.detail is not None
    assert "VM.Allocate" in operation.detail
    assert WRONG_TOKEN not in operation.detail
    assert TOKEN not in operation.detail


def test_a_power_refusal_still_names_the_power_permission(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.power_forced_status = 403

    operation = provider.request_power(configuration, "100", PowerAction.STOP)

    assert operation.detail is not None
    assert "VM.PowerMgmt" in operation.detail
    assert "VM.Allocate" not in operation.detail


@pytest.mark.parametrize("missing", ["kind", "node", "storage", "disk_gb"])
def test_a_missing_value_gives_a_failed_operation_and_sends_nothing(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
    missing: str,
) -> None:
    desired = replace(
        PROXMOX_DESIRED,
        values=tuple(v for v in PROXMOX_DESIRED.values if v.name != missing),
    )

    operation = provider.request_provision(configuration, desired, SERVER_NAME, "102")

    assert operation.state is OperationState.FAILED
    assert operation.detail is not None
    assert missing in operation.detail
    assert mock_proxmox.write_requests == []


def test_a_storage_of_another_node_gives_a_failed_operation_and_sends_nothing(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    desired = with_values(PROXMOX_DESIRED, storage="pve2/local")

    operation = provider.request_provision(configuration, desired, SERVER_NAME, "102")

    assert operation.state is OperationState.FAILED
    assert operation.detail is not None
    assert "storage" in operation.detail
    assert mock_proxmox.write_requests == []


def test_an_image_of_another_node_gives_a_failed_operation_and_sends_nothing(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    desired = replace(PROXMOX_DESIRED, image=EXPECTED_TEMPLATES[2].value)

    operation = provider.request_provision(configuration, desired, SERVER_NAME, "102")

    assert operation.state is OperationState.FAILED
    assert operation.detail is not None
    assert "image" in operation.detail
    assert mock_proxmox.write_requests == []


def test_the_neutral_image_is_a_rule_and_not_a_value() -> None:
    names = {rule.name for rule in value_rules()}

    assert len(names & set(IMAGE_RULE_NAMES)) == 1
    assert "template" in names
    assert not {v.name for v in PROXMOX_DESIRED.values} & set(IMAGE_RULE_NAMES)


def test_an_unknown_kind_gives_a_failed_operation_and_sends_nothing(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    desired = with_values(PROXMOX_DESIRED, kind="vz")

    operation = provider.request_provision(configuration, desired, SERVER_NAME, "102")

    assert operation.state is OperationState.FAILED
    assert mock_proxmox.write_requests == []
