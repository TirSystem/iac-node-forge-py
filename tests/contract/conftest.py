"""Fixtures: the mock Proxmox VE API with data, and the provider cases."""

from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from iac_node_forge.application.ports import ProviderPort
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_option import ServerOption
from iac_node_forge.infrastructure.proxmox import ProxmoxProvider
from tests.contract.mock_proxmox import MockProxmoxServer
from tests.unit.application.fakes import FakeProvider

TOKEN = "root@pam!forge=11111111-2222-3333-4444-555555555555"
WRONG_TOKEN = "root@pam!forge=99999999-9999-9999-9999-999999999999"
MIB = 1024 * 1024

EXPECTED_SERVERS = (
    Server("100", "web", "running", "2 vCPU, 2048 MiB", (), False),
    Server("101", "db", "stopped", "4 vCPU, 8192 MiB", (), False),
)
EXPECTED_NODES = (ServerOption("node", "pve1"), ServerOption("node", "pve2"))
EXPECTED_STORAGE = (
    ServerOption("storage", "pve1/local"),
    ServerOption("storage", "pve1/local-lvm"),
    ServerOption("storage", "pve2/local"),
)
READ_ONLY_CAPABILITIES = frozenset(
    Capability(name)
    for name in ("list_servers", "list_server_options", "inspect_server")
)


@dataclass(frozen=True, slots=True)
class ProviderCase:
    """A ProviderPort implementation with the data both implementations serve."""

    provider: ProviderPort
    configuration: ProviderConfiguration
    token: str
    wrong_token: str


@pytest.fixture
def mock_proxmox() -> Iterator[MockProxmoxServer]:
    with MockProxmoxServer(TOKEN) as server:
        state = server.state
        state.nodes = [{"node": "pve1"}, {"node": "pve2"}]
        state.storages = {
            "pve1": [{"storage": "local"}, {"storage": "local-lvm"}],
            "pve2": [{"storage": "local"}],
        }
        state.resources = [
            {
                "id": "qemu/100",
                "type": "qemu",
                "vmid": 100,
                "name": "web",
                "status": "running",
                "maxcpu": 2,
                "maxmem": 2048 * MIB,
            },
            {
                "id": "lxc/101",
                "type": "lxc",
                "vmid": 101,
                "name": "db",
                "status": "stopped",
                "maxcpu": 4,
                "maxmem": 8192 * MIB,
            },
        ]
        yield server


def configuration_for(address: str) -> ProviderConfiguration:
    return ProviderConfiguration(
        ConfigurationId("cfg-1"), ProviderKind.PROXMOX_VE, "Lab", address
    )


@pytest.fixture
def proxmox_provider(mock_proxmox: MockProxmoxServer) -> ProxmoxProvider:
    return ProxmoxProvider(Credentials.single(TOKEN), timeout=2.0)


@pytest.fixture(params=["fake", "proxmox"])
def provider_case(
    request: pytest.FixtureRequest, mock_proxmox: MockProxmoxServer
) -> ProviderCase:
    if request.param == "proxmox":
        provider: ProviderPort = ProxmoxProvider(Credentials.single(TOKEN), timeout=2.0)
    else:
        provider = FakeProvider(
            accepted_secret=TOKEN,
            capabilities=READ_ONLY_CAPABILITIES,
            options=EXPECTED_NODES + EXPECTED_STORAGE,
            servers=EXPECTED_SERVERS,
        )
    return ProviderCase(
        provider,
        configuration_for(mock_proxmox.address),
        TOKEN,
        WRONG_TOKEN,
    )
