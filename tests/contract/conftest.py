"""Fixtures: the mock provider APIs with data, and the provider variants."""

from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

import pytest

from iac_node_forge.application.capabilities import (
    POWER_CAPABILITIES,
    PROVISION_SERVER,
    REMOVE_SERVER,
)
from iac_node_forge.application.errors import (
    ProviderAuthenticationError,
    ProviderTimeoutError,
)
from iac_node_forge.application.ports import RemovalBlock
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.power_action import POWER_ACTIONS, PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.provider_value import ProviderValue
from iac_node_forge.domain.removal_kind import RemovalKind
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_option import ServerOption
from iac_node_forge.infrastructure.contabo import ContaboProvider
from iac_node_forge.infrastructure.proxmox import ProxmoxProvider
from tests.contract.mock_contabo import MockContaboServer
from tests.contract.mock_proxmox import MockProxmoxServer
from tests.contract.provider_under_test import (
    ProviderUnderTest,
    ProvisionCase,
    RemovalCase,
)
from tests.contract.transfer_under_test import FakeWorld, SshdWorld, TransferWorld
from tests.integration import sshd_server
from tests.integration.sshd_server import SshdServer
from tests.unit.application.fakes import (
    FakePowerProvider,
    FakeProvider,
    FakeProvisionProvider,
    FakeRemovalProvider,
)

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
EXPECTED_TEMPLATES = (
    ServerOption("template", "pve1/local:vztmpl/alpine-3.24-default.tar.xz"),
    ServerOption("template", "pve1/local:iso/debian-12.iso"),
    ServerOption("template", "pve2/local:vztmpl/debian-12.tar.zst"),
)
EXPECTED_PRODUCTS = (ServerOption("product", "V45"), ServerOption("product", "V47"))
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
PROVISION_CAPABILITY = frozenset({Capability(PROVISION_SERVER)})
REMOVAL_CAPABILITY = frozenset({Capability(REMOVE_SERVER)})
ALL_CAPABILITIES = (
    READ_ONLY_CAPABILITIES
    | POWER_ONLY_CAPABILITIES
    | PROVISION_CAPABILITY
    | REMOVAL_CAPABILITY
)
PROXMOX_VERBS = {
    PowerAction.START: "start",
    PowerAction.SHUTDOWN: "shutdown",
    PowerAction.STOP: "stop",
    PowerAction.RESTART: "reboot",
}
PROXMOX_PLACES = {"100": ("pve1", "qemu"), "101": ("pve2", "lxc")}
CONTABO_CREATE_WRITE = ("POST", "/v1/compute/instances")
CONTABO_CANCEL_WRITES = frozenset(
    ("POST", f"/v1/compute/instances/{server}/cancel") for server in ("100", "101")
)
CONTABO_ALLOWED_WRITES = (
    frozenset(
        ("POST", f"/v1/compute/instances/{server}/actions/{verb}")
        for server in ("100", "101")
        for verb in ("start", "shutdown", "stop", "restart")
    )
    | {CONTABO_CREATE_WRITE}
    | CONTABO_CANCEL_WRITES
)
PROXMOX_CREATE_WRITES = frozenset(
    ("POST", f"/api2/json/nodes/{node}/{kind}")
    for node in ("pve1", "pve2")
    for kind in ("lxc", "qemu")
)
PROXMOX_DELETE_WRITES = frozenset(
    ("DELETE", f"/api2/json/nodes/{node}/{kind}/{server}")
    for server, (node, kind) in PROXMOX_PLACES.items()
)
PROXMOX_ALLOWED_WRITES = (
    frozenset(
        ("POST", f"/api2/json/nodes/{node}/{kind}/{server}/status/{verb}")
        for server, (node, kind) in PROXMOX_PLACES.items()
        for verb in PROXMOX_VERBS.values()
    )
    | PROXMOX_CREATE_WRITES
    | PROXMOX_DELETE_WRITES
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
            "pve1": [
                {"storage": "local", "content": "iso,vztmpl,backup"},
                {"storage": "local-lvm", "content": "rootdir,images"},
            ],
            "pve2": [{"storage": "local", "content": "iso,vztmpl"}],
        }
        state.storage_content = {
            "pve1/local": [
                {"volid": "local:iso/debian-12.iso", "content": "iso"},
                {
                    "volid": "local:vztmpl/alpine-3.24-default.tar.xz",
                    "content": "vztmpl",
                },
            ],
            "pve2/local": [
                {"volid": "local:vztmpl/debian-12.tar.zst", "content": "vztmpl"}
            ],
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
        state.products = [
            {"productId": option.value, "name": f"product {number}"}
            for number, option in enumerate(EXPECTED_PRODUCTS, start=1)
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
        options=EXPECTED_NODES + EXPECTED_STORAGE + EXPECTED_TEMPLATES,
        servers=EXPECTED_SERVERS,
    )
    return ProviderUnderTest(
        name="fake",
        provider=provider,
        configuration=configuration_for("fake.invalid"),
        credentials=Credentials.single(TOKEN),
        wrong_credentials=Credentials.single(WRONG_TOKEN),
        expected_servers=EXPECTED_SERVERS,
        expected_options={
            "node": EXPECTED_NODES,
            "storage": EXPECTED_STORAGE,
            "template": EXPECTED_TEMPLATES,
        },
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
            for action in POWER_ACTIONS
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
        expected_options={
            "node": EXPECTED_NODES,
            "storage": EXPECTED_STORAGE,
            "template": EXPECTED_TEMPLATES,
        },
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
        expected_options={
            "region": EXPECTED_REGIONS,
            "image": EXPECTED_IMAGES,
            "product": EXPECTED_PRODUCTS,
        },
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


SERVER_NAME = "new-server"
PROXMOX_DESIRED = DesiredConfiguration(
    ConfigurationId("cfg-1"),
    "alpine-small",
    EXPECTED_TEMPLATES[0].value,
    values=(
        ProviderValue("kind", "lxc"),
        ProviderValue("node", "pve1"),
        ProviderValue("storage", "pve1/local-lvm"),
        ProviderValue("memory_mb", "512"),
        ProviderValue("cores", "1"),
        ProviderValue("disk_gb", "4"),
    ),
)
CONTABO_DESIRED = DesiredConfiguration(
    ConfigurationId("cfg-1"),
    "vps-small",
    EXPECTED_IMAGES[0].value,
    values=(
        ProviderValue("region", "EU1"),
        ProviderValue("product", "V45"),
        ProviderValue("period", "12"),
    ),
)
PROXMOX_VALUE_NAMES = frozenset(
    ("kind", "node", "storage", "memory_mb", "cores", "disk_gb")
)
CONTABO_VALUE_NAMES = frozenset(("region", "product", "period"))


def _fake_provision_case() -> ProvisionCase:
    fake = FakeProvisionProvider(
        servers=EXPECTED_SERVERS, options=EXPECTED_NODES + EXPECTED_STORAGE
    )
    return ProvisionCase(
        name="fake",
        provider=fake,
        provision=fake,
        configuration=configuration_for("fake.invalid"),
        desired=PROXMOX_DESIRED,
        server_name=SERVER_NAME,
        required_value_names=frozenset(("node", "storage", "cores")),
        secret_values=(TOKEN, WRONG_TOKEN),
        reject_creation=lambda: fake.reject_with(
            ProviderAuthenticationError("the fake provider answered 403")
        ),
        rejection_hint=None,
        fail_operations=fake.make_operations_fail,
        write_requests=lambda: [("REQUEST", name) for name, _ in fake.requests],
        allowed_write_requests=frozenset({("REQUEST", SERVER_NAME)}),
        reserves_identifier=True,
        states_cost=False,
    )


def _proxmox_provision_case(mock: MockProxmoxServer) -> ProvisionCase:
    provider = ProxmoxProvider(Credentials.single(TOKEN), timeout=2.0)
    return ProvisionCase(
        name="proxmox",
        provider=provider,
        provision=provider,
        configuration=configuration_for(mock.address),
        desired=PROXMOX_DESIRED,
        server_name=SERVER_NAME,
        required_value_names=PROXMOX_VALUE_NAMES,
        secret_values=(TOKEN, TOKEN.split("=", 1)[1]),
        reject_creation=lambda: setattr(mock.state, "create_forced_status", 403),
        rejection_hint="VM.Allocate",
        fail_operations=lambda: setattr(
            mock.state, "task_exitstatus", "unable to create CT 102 - no space"
        ),
        write_requests=lambda: [(r.method, r.path) for r in mock.write_requests],
        allowed_write_requests=PROXMOX_ALLOWED_WRITES,
        reserves_identifier=True,
        states_cost=False,
    )


def _contabo_provision_case(mock: MockContaboServer) -> ProvisionCase:
    provider = ContaboProvider(
        Credentials(CONTABO_VALUES), auth_url=mock.auth_url, timeout=2.0
    )
    return ProvisionCase(
        name="contabo",
        provider=provider,
        provision=provider,
        configuration=contabo_configuration_for(mock.address),
        desired=CONTABO_DESIRED,
        server_name=SERVER_NAME,
        required_value_names=CONTABO_VALUE_NAMES,
        secret_values=tuple(CONTABO_VALUES.values()),
        reject_creation=lambda: setattr(mock.state, "create_forced_status", 403),
        rejection_hint=None,
        fail_operations=lambda: setattr(mock.state, "transition_fails", True),
        write_requests=lambda: [(r.method, r.path) for r in mock.write_requests],
        allowed_write_requests=frozenset({CONTABO_CREATE_WRITE}),
        reserves_identifier=False,
        states_cost=True,
    )


@pytest.fixture(params=["fake", "proxmox", "contabo"])
def provision_case(request: pytest.FixtureRequest) -> ProvisionCase:
    """The same provisioning in each variant; the mocks start on demand."""
    if request.param == "proxmox":
        return _proxmox_provision_case(request.getfixturevalue("mock_proxmox"))
    if request.param == "contabo":
        return _contabo_provision_case(request.getfixturevalue("mock_contabo"))
    return _fake_provision_case()


def _time_out(*args: object, **kwargs: object) -> NoReturn:
    raise ProviderTimeoutError("the provider did not answer within 2.0 seconds")


def _fake_removal_case() -> RemovalCase:
    fake = FakeRemovalProvider(servers=EXPECTED_SERVERS)
    return RemovalCase(
        name="fake",
        provider=fake,
        removal=fake,
        configuration=configuration_for("fake.invalid"),
        secret_values=(TOKEN, WRONG_TOKEN),
        kind=RemovalKind.DESTROY,
        removable_id="101",
        blocked_id="100",
        expected_block=RemovalBlock.RUNNING,
        prepare_blocked=lambda: None,
        reject_removal=lambda: fake.reject_with(
            ProviderAuthenticationError("the fake provider answered 403")
        ),
        rejection_hint=None,
        fail_operations=fake.make_operations_fail,
        lose_answers=fake.answer_unknown,
        write_requests=lambda: [("REQUEST", server) for server in fake.requests],
        allowed_write_requests=frozenset(
            ("REQUEST", server) for server in ("100", "101")
        ),
    )


def _proxmox_removal_case(mock: MockProxmoxServer) -> RemovalCase:
    provider = ProxmoxProvider(Credentials.single(TOKEN), timeout=2.0)
    return RemovalCase(
        name="proxmox",
        provider=provider,
        removal=provider,
        configuration=configuration_for(mock.address),
        secret_values=(TOKEN, TOKEN.split("=", 1)[1]),
        kind=RemovalKind.DESTROY,
        removable_id="101",
        blocked_id="100",
        expected_block=RemovalBlock.RUNNING,
        prepare_blocked=lambda: None,
        reject_removal=lambda: setattr(mock.state, "delete_forced_status", 403),
        rejection_hint="VM.Allocate",
        fail_operations=lambda: setattr(
            mock.state, "task_exitstatus", "unable to destroy CT 101 - busy"
        ),
        lose_answers=lambda: setattr(provider, "_delete", _time_out),
        write_requests=lambda: [(r.method, r.path) for r in mock.write_requests],
        allowed_write_requests=PROXMOX_DELETE_WRITES,
    )


def _contabo_removal_case(mock: MockContaboServer) -> RemovalCase:
    provider = ContaboProvider(
        Credentials(CONTABO_VALUES), auth_url=mock.auth_url, timeout=2.0
    )
    return RemovalCase(
        name="contabo",
        provider=provider,
        removal=provider,
        configuration=contabo_configuration_for(mock.address),
        secret_values=tuple(CONTABO_VALUES.values()),
        kind=RemovalKind.CANCEL,
        removable_id="101",
        blocked_id="100",
        expected_block=RemovalBlock.ALREADY_REMOVED,
        prepare_blocked=lambda: mock.state.instances[0].update(status="cancelled"),
        reject_removal=lambda: setattr(mock.state, "cancel_forced_status", 403),
        rejection_hint=None,
        fail_operations=lambda: setattr(mock.state, "transition_fails", True),
        lose_answers=lambda: setattr(provider, "_authorised_post", _time_out),
        write_requests=lambda: [(r.method, r.path) for r in mock.write_requests],
        allowed_write_requests=CONTABO_CANCEL_WRITES,
    )


@pytest.fixture(params=["fake", "proxmox", "contabo"])
def removal_case(request: pytest.FixtureRequest) -> RemovalCase:
    """The same removal in each variant; the mocks start on demand."""
    if request.param == "proxmox":
        return _proxmox_removal_case(request.getfixturevalue("mock_proxmox"))
    if request.param == "contabo":
        return _contabo_removal_case(request.getfixturevalue("mock_contabo"))
    return _fake_removal_case()


@pytest.fixture(scope="session")
def sshd(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SshdServer]:
    """The throw-away sshd container, started once for the session on demand."""
    reason = sshd_server.unavailable_reason()
    if reason is not None:
        pytest.skip(reason)
    with sshd_server.running_sshd(tmp_path_factory.mktemp("sshd")) as server:
        yield server


@pytest.fixture(params=["fake", pytest.param("openssh", marks=pytest.mark.integration)])
def world(request: pytest.FixtureRequest, tmp_path: Path) -> TransferWorld:
    """The transfer ports in the fake and, opt-in, against a real OpenSSH server."""
    if request.param == "openssh":
        return SshdWorld(request.getfixturevalue("sshd"), tmp_path)
    return FakeWorld()
