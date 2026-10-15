"""Fixtures: the mock provider APIs with data, and the provider variants."""

from collections.abc import Iterator

import pytest

from iac_node_forge.application.capabilities import POWER_CAPABILITIES
from iac_node_forge.application.errors import ProviderAuthenticationError
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_option import ServerOption
from iac_node_forge.infrastructure.contabo import ContaboProvider
from iac_node_forge.infrastructure.proxmox import ProxmoxProvider
from tests.contract.mock_contabo import MockContaboServer
from tests.contract.mock_proxmox import MockProxmoxServer
from tests.contract.provider_under_test import ProviderUnderTest
from tests.unit.application.fakes import FakePowerProvider, FakeProvider

TOKEN = "root@pam!forge=11111111-2222-3333-4444-555555555555"
WRONG_TOKEN = "root@pam!forge=99999999-9999-9999-9999-999999999999"
MIB = 1024 * 1024

CONTABO_VALUES = {
    "client_id": "INT-12345678",
    "client_secret": "cs-7f3a9c1e5b2d4086",
    "api_user": "forge.user@example.org",
    "api_password": "pw-Zk29!qLm83vTx",
}
CONTABO_WRONG_VALUES = {**CONTABO_VALUES, "api_password": "pw-WRONG-4d2e8a61!"}

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
EXPECTED_CONTABO_SERVERS = (
    Server(
        "100",
        "web",
        "running",
        "2 vCPU, 2048 MiB",
        ("203.0.113.10", "2001:db8::10", "203.0.113.11"),
        False,
    ),
    Server("101", "db", "stopped", "4 vCPU, 8192 MiB", ("203.0.113.20",), False),
)
EXPECTED_REGIONS = (ServerOption("region", "EU1"), ServerOption("region", "US-east"))
EXPECTED_IMAGES = (
    ServerOption("image", "3f1c0a52-0000-4000-8000-000000000001"),
    ServerOption("image", "3f1c0a52-0000-4000-8000-000000000002"),
)
READ_ONLY_CAPABILITIES = frozenset(
    Capability(name)
    for name in ("list_servers", "list_server_options", "inspect_server")
)
POWER_ONLY_CAPABILITIES = frozenset(Capability(name) for name in POWER_CAPABILITIES)
ALL_CAPABILITIES = READ_ONLY_CAPABILITIES | POWER_ONLY_CAPABILITIES
PROXMOX_VERBS = {
    PowerAction.START: "start",
    PowerAction.SHUTDOWN: "shutdown",
    PowerAction.STOP: "stop",
    PowerAction.RESTART: "reboot",
}
PROXMOX_PLACES = {"100": ("pve1", "qemu"), "101": ("pve2", "lxc")}
CONTABO_ALLOWED_WRITES = frozenset(
    ("POST", f"/v1/compute/instances/{server}/actions/{verb}")
    for server in ("100", "101")
    for verb in ("start", "shutdown", "stop", "restart")
)
PROXMOX_ALLOWED_WRITES = frozenset(
    ("POST", f"/api2/json/nodes/{node}/{kind}/{server}/status/{verb}")
    for server, (node, kind) in PROXMOX_PLACES.items()
    for verb in PROXMOX_VERBS.values()
)


def configuration_for(
    address: str, kind: ProviderKind = ProviderKind.PROXMOX_VE
) -> ProviderConfiguration:
    return ProviderConfiguration(ConfigurationId("cfg-1"), kind, "Lab", address)


def contabo_configuration_for(address: str) -> ProviderConfiguration:
    return configuration_for(address, ProviderKind.CONTABO)


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
                "node": "pve1",
                "type": "qemu",
                "vmid": 100,
                "name": "web",
                "status": "running",
                "maxcpu": 2,
                "maxmem": 2048 * MIB,
            },
            {
                "id": "lxc/101",
                "node": "pve2",
                "type": "lxc",
                "vmid": 101,
                "name": "db",
                "status": "stopped",
                "maxcpu": 4,
                "maxmem": 8192 * MIB,
            },
        ]
        yield server


@pytest.fixture
def mock_contabo() -> Iterator[MockContaboServer]:
    with MockContaboServer(**CONTABO_VALUES) as server:
        state = server.state
        state.data_centers = [
            {"name": "European Union 1", "slug": "EU1", "regionSlug": "EU"},
            {"name": "United States East", "slug": "US-east", "regionSlug": "US-east"},
        ]
        state.images = [
            {"imageId": option.value, "name": f"image {number}"}
            for number, option in enumerate(EXPECTED_IMAGES, start=1)
        ]
        state.instances = [
            {
                "instanceId": 100,
                "name": "vmd100",
                "displayName": "web",
                "status": "running",
                "cpuCores": 2,
                "ramMb": 2048,
                "ipConfig": {
                    "v4": {"ip": "203.0.113.10"},
                    "v6": {"ip": "2001:db8::10"},
                },
                "additionalIps": [{"v4": {"ip": "203.0.113.11"}}],
            },
            {
                "instanceId": 101,
                "name": "db",
                "displayName": "",
                "status": "stopped",
                "cpuCores": 4,
                "ramMb": 8192,
                "ipConfig": {"v4": {"ip": "203.0.113.20"}},
                "additionalIps": [],
            },
        ]
        yield server


@pytest.fixture
def proxmox_provider(mock_proxmox: MockProxmoxServer) -> ProxmoxProvider:
    return ProxmoxProvider(Credentials.single(TOKEN), timeout=2.0)


@pytest.fixture
def contabo_provider(mock_contabo: MockContaboServer) -> ContaboProvider:
    return ContaboProvider(
        Credentials(CONTABO_VALUES), auth_url=mock_contabo.auth_url, timeout=2.0
    )


def _fake_variant() -> ProviderUnderTest:
    power = FakePowerProvider()
    provider = FakeProvider(
        accepted_secret=TOKEN,
        capabilities=ALL_CAPABILITIES,
        options=EXPECTED_NODES + EXPECTED_STORAGE,
        servers=EXPECTED_SERVERS,
    )
    return ProviderUnderTest(
        name="fake",
        provider=provider,
        configuration=configuration_for("fake.invalid"),
        credentials=Credentials.single(TOKEN),
        wrong_credentials=Credentials.single(WRONG_TOKEN),
        expected_servers=EXPECTED_SERVERS,
        expected_options={"node": EXPECTED_NODES, "storage": EXPECTED_STORAGE},
        secret_values=(TOKEN, WRONG_TOKEN),
        power=power,
        reject_power_requests=lambda: power.reject_with(
            ProviderAuthenticationError("the fake provider answered 403")
        ),
        fail_operations=power.make_operations_fail,
        write_requests=lambda: [
            ("REQUEST", f"{server}:{action.value}") for server, action in power.requests
        ],
        allowed_write_requests=frozenset(
            ("REQUEST", f"{server}:{action.value}")
            for server in ("100", "101")
            for action in PowerAction
        ),
    )


def _proxmox_variant(mock: MockProxmoxServer) -> ProviderUnderTest:
    provider = ProxmoxProvider(Credentials.single(TOKEN), timeout=2.0)
    return ProviderUnderTest(
        name="proxmox",
        provider=provider,
        configuration=configuration_for(mock.address),
        credentials=Credentials.single(TOKEN),
        wrong_credentials=Credentials.single(WRONG_TOKEN),
        expected_servers=EXPECTED_SERVERS,
        expected_options={"node": EXPECTED_NODES, "storage": EXPECTED_STORAGE},
        secret_values=(
            TOKEN,
            WRONG_TOKEN,
            TOKEN.split("=", 1)[1],
            WRONG_TOKEN.split("=", 1)[1],
        ),
        power=provider,
        reject_power_requests=lambda: setattr(mock.state, "power_forced_status", 403),
        fail_operations=lambda: setattr(
            mock.state, "task_exitstatus", "command 'qm start 100' failed"
        ),
        write_requests=lambda: [(r.method, r.path) for r in mock.write_requests],
        allowed_write_requests=PROXMOX_ALLOWED_WRITES,
    )


def _contabo_variant(mock: MockContaboServer) -> ProviderUnderTest:
    provider = ContaboProvider(
        Credentials(CONTABO_VALUES),
        auth_url=mock.auth_url,
        timeout=2.0,
        restart_settle_seconds=0.0,
    )
    return ProviderUnderTest(
        name="contabo",
        provider=provider,
        configuration=contabo_configuration_for(mock.address),
        credentials=Credentials(CONTABO_VALUES),
        wrong_credentials=Credentials(CONTABO_WRONG_VALUES),
        expected_servers=EXPECTED_CONTABO_SERVERS,
        expected_options={"region": EXPECTED_REGIONS, "image": EXPECTED_IMAGES},
        secret_values=tuple(CONTABO_VALUES.values())
        + (CONTABO_WRONG_VALUES["api_password"],),
        power=provider,
        reject_power_requests=lambda: setattr(mock.state, "power_forced_status", 403),
        fail_operations=lambda: setattr(mock.state, "transition_fails", True),
        write_requests=lambda: [(r.method, r.path) for r in mock.write_requests],
        allowed_write_requests=CONTABO_ALLOWED_WRITES,
    )


@pytest.fixture(params=["fake", "proxmox", "contabo"])
def provider_under_test(request: pytest.FixtureRequest) -> ProviderUnderTest:
    """The same logical provider in each variant; the mocks start on demand."""
    if request.param == "proxmox":
        return _proxmox_variant(request.getfixturevalue("mock_proxmox"))
    if request.param == "contabo":
        return _contabo_variant(request.getfixturevalue("mock_contabo"))
    return _fake_variant()
