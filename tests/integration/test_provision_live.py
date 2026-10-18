"""Live provisioning test: it CREATES A REAL CONTAINER, then deletes it.

Only Proxmox VE is tested live. Contabo is never created live: a Contabo
instance costs money and has a minimum term.

Nothing runs unless every variable is set: ``PVE_TOKEN`` (full
``USER@REALM!TOKENID=SECRET``), ``PVE_PROVISION_TEST_NODE`` (the node name),
``PVE_PROVISION_TEST_STORAGE`` (the storage of the root disk, for example
``local``), ``PVE_PROVISION_TEST_TEMPLATE`` (a container template volume id on
that node, for example ``local:vztmpl/alpine-3.24-default_20260714_amd64.tar.xz``)
and ``PVE_PROVISION_TEST_CONFIRM``, which must equal
``create-server-iacnf-live``. ``PVE_ADDRESS`` and ``PVE_VERIFY`` are optional,
as in ``test_proxmox_live.py``. Run it only against a disposable Proxmox VE.

The container is named ``iacnf-live-<8 hex>``, new on every run. The product
code has no delete: the test-only cleanup below removes the container this run
created, and only that one, even when an assertion fails. No credential value
is printed, logged or put in an assertion message.
"""

import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from iac_node_forge.application.dto import (
    Confirmation,
    ProvisionOutcome,
)
from iac_node_forge.application.ports import (
    ProviderPort,
    ServerPowerPort,
    ServerProvisionPort,
    ServerRemovalPort,
)
from iac_node_forge.application.use_cases.define_desired_configuration import (
    DefineDesiredConfiguration,
)
from iac_node_forge.application.use_cases.desired_configuration_validator import (
    DesiredConfigurationValidator,
)
from iac_node_forge.application.use_cases.provision_server import ProvisionServer
from iac_node_forge.application.use_cases.track_operation import TrackOperation
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.operation import OperationState
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.infrastructure.configuration import (
    JsonLinesConfigurationStore,
    JsonLinesDesiredConfigurationStore,
    JsonLinesOperationStore,
    JsonLinesProvisionIntentStore,
)
from iac_node_forge.infrastructure.proxmox import ProxmoxProvider

pytestmark = pytest.mark.integration

DEFAULT_PVE_ADDRESS = "https://localhost:8006"
CONFIRM_TEXT = "create-server-iacnf-live"
NAME_PREFIX = "iacnf-live-"
CONFIGURATION_ID = ConfigurationId("live-provision")
VARIABLES = (
    "PVE_TOKEN",
    "PVE_PROVISION_TEST_NODE",
    "PVE_PROVISION_TEST_STORAGE",
    "PVE_PROVISION_TEST_TEMPLATE",
    "PVE_PROVISION_TEST_CONFIRM",
)
TIMEOUT_SECONDS = 20.0
API_PREFIX = "/api2/json"
SECRET_SEPARATOR = "="
TASK_WAIT_SECONDS = 120.0
TASK_POLL_SECONDS = 2.0
DELETE_RETRY_SECONDS = 120.0
DELETE_QUERY = "purge=1&destroy-unreferenced-disks=1"


def _verify_from_environment() -> bool | str:
    value = os.environ.get("PVE_VERIFY", "true").strip()
    lowered = value.lower()
    if lowered in ("", "true"):
        return True
    if lowered == "false":
        return False
    return value


class _TestOnlyCleanup:
    """Deletes the container this run created; the product code cannot delete.

    It touches a guest only when its name is this run's name (which starts with
    ``iacnf-live-``), it is on the test node and, once the operation named a
    vmid, it has that vmid. It sends the token only to the configured address.
    """

    def __init__(
        self,
        address: str,
        token: str,
        verify: bool | str,
        node: str,
        run_name: str,
    ) -> None:
        self._address = address.rstrip("/")
        self._token = token
        self._verify = verify
        self._node = node
        self._run_name = run_name
        self.known_vmids: set[str] = set()

    def run(self) -> None:
        """Stop and delete this run's container; fail when one is left."""
        if not self._run_name.startswith(NAME_PREFIX):
            pytest.fail("the cleanup refuses a name without the live prefix")
        for vmid in self._candidates():
            self._delete(vmid)
        left = self._candidates()
        assert not left, "a container created by this run could not be deleted"

    def _call(self, method: str, path: str) -> httpx.Response:
        url = self._address + API_PREFIX + path
        headers = {"Authorization": f"PVEAPIToken={self._token}"}
        return httpx.request(
            method,
            url,
            headers=headers,
            verify=self._verify,
            timeout=TIMEOUT_SECONDS,
        )

    def _candidates(self) -> list[str]:
        response = self._call("GET", "/cluster/resources?type=vm")
        data = response.json().get("data", []) if response.is_success else []
        return [
            str(item["vmid"])
            for item in data
            if item.get("name") == self._run_name
            and item.get("node") == self._node
            and item.get("type") == "lxc"
            and (not self.known_vmids or str(item.get("vmid")) in self.known_vmids)
        ]

    def _delete(self, vmid: str) -> None:
        base = f"/nodes/{quote(self._node, safe='')}/lxc/{int(vmid)}"
        status = self._call("GET", base + "/status/current")
        if status.is_success and status.json().get("data", {}).get("status") != (
            "stopped"
        ):
            self._wait_task(self._call("POST", base + "/status/stop"))
        deadline = time.monotonic() + DELETE_RETRY_SECONDS
        while True:
            # A create task that is still running locks the container.
            response = self._call("DELETE", f"{base}?{DELETE_QUERY}")
            if response.is_success:
                self._wait_task(response)
                return
            if time.monotonic() >= deadline:
                pytest.fail(f"deleting container {vmid} failed: {response.status_code}")
            time.sleep(TASK_POLL_SECONDS)

    def _wait_task(self, response: httpx.Response) -> None:
        if not response.is_success:
            return
        upid = response.json().get("data")
        if not isinstance(upid, str) or not upid.startswith("UPID:"):
            return
        path = f"/nodes/{quote(self._node, safe='')}/tasks/"
        path += f"{quote(upid, safe=':')}/status"
        deadline = time.monotonic() + TASK_WAIT_SECONDS
        while time.monotonic() < deadline:
            status = self._call("GET", path)
            if status.is_success and status.json().get("data", {}).get("status") == (
                "stopped"
            ):
                return
            time.sleep(TASK_POLL_SECONDS)


class _FixedResolver:
    """ProviderResolver that hands out the one real adapter for every port."""

    def __init__(self, provider: ProxmoxProvider) -> None:
        self._provider = provider

    def provider_for(self, configuration: ProviderConfiguration) -> ProviderPort:
        return self._provider

    def power_for(self, configuration: ProviderConfiguration) -> ServerPowerPort:
        return self._provider

    def provision_for(
        self, configuration: ProviderConfiguration
    ) -> ServerProvisionPort:
        return self._provider

    def removal_for(self, configuration: ProviderConfiguration) -> ServerRemovalPort:
        return self._provider


@dataclass(frozen=True, slots=True, repr=False)
class ProvisionTarget:
    """The real Proxmox VE and the use cases wired to its adapter.

    It hides its credential values from any output.
    """

    provider: ProxmoxProvider
    configuration: ProviderConfiguration
    directory: Path
    run_name: str
    node: str
    values: dict[str, str]
    image: str
    cleanup: _TestOnlyCleanup
    secrets: tuple[str, ...] = field(repr=False)

    def __repr__(self) -> str:
        return f"ProvisionTarget(name={self.run_name!r})"

    def leaks(self, text: str) -> bool:
        """Say whether ``text`` contains any credential value."""
        return any(secret in text for secret in self.secrets)

    @property
    def store_path(self) -> Path:
        return self.directory / "configuration.jsonl"


def _skip_unless_ready() -> None:
    missing = [name for name in VARIABLES if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip("no live provisioning opt-in; not set: " + ", ".join(missing))
    if os.environ["PVE_PROVISION_TEST_CONFIRM"].strip() != CONFIRM_TEXT:
        pytest.skip("confirmation text does not match")


def _option_value(node: str, name: str) -> str:
    """The option value the adapter lists: ``<node>/<name>``."""
    return name if name.startswith(f"{node}/") else f"{node}/{name}"


@pytest.fixture(scope="module")
def target(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ProvisionTarget]:
    _skip_unless_ready()
    token = os.environ["PVE_TOKEN"].strip()
    node = os.environ["PVE_PROVISION_TEST_NODE"].strip()
    storage = os.environ["PVE_PROVISION_TEST_STORAGE"].strip()
    template = os.environ["PVE_PROVISION_TEST_TEMPLATE"].strip()
    address = os.environ.get("PVE_ADDRESS", "").strip() or DEFAULT_PVE_ADDRESS
    verify = _verify_from_environment()
    run_name = f"{NAME_PREFIX}{uuid.uuid4().hex[:8]}"
    configuration = ProviderConfiguration(
        CONFIGURATION_ID, ProviderKind.PROXMOX_VE, "Live provision Proxmox VE", address
    )
    provider = ProxmoxProvider(
        Credentials.single(token), verify=verify, timeout=TIMEOUT_SECONDS
    )
    # A real adapter is required: a silently substituted double would pass.
    assert type(provider) is ProxmoxProvider
    _, _, secret = token.partition(SECRET_SEPARATOR)
    cleanup = _TestOnlyCleanup(address, token, verify, node, run_name)
    live = ProvisionTarget(
        provider,
        configuration,
        tmp_path_factory.mktemp("provision-proxmox"),
        run_name,
        node,
        {
            "kind": "lxc",
            "node": node,
            "storage": _option_value(node, storage),
            "memory_mb": "128",
            "cores": "1",
            "disk_gb": "1",
        },
        _option_value(node, template),
        cleanup,
        tuple(text for text in (token, secret) if text),
    )
    try:
        yield live
    finally:
        cleanup.run()


@pytest.fixture
def writes_sent(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Record every request that is neither a read nor a token request."""
    writes: list[str] = []
    original = httpx.Client.send

    def recording_send(
        self: httpx.Client, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        if request.method not in ("GET", "HEAD") and "token" not in request.url.path:
            writes.append(request.method)
        return original(self, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", recording_send)
    yield writes


def test_provision_proxmox_lxc_creates_records_and_refuses_duplicate(
    target: ProvisionTarget, writes_sent: list[str]
) -> None:
    resolver = _FixedResolver(target.provider)
    configurations = JsonLinesConfigurationStore(target.store_path)
    desired = JsonLinesDesiredConfigurationStore(target.store_path)
    intents = JsonLinesProvisionIntentStore(target.store_path)
    operations = JsonLinesOperationStore(target.store_path)
    configurations.save(target.configuration)
    validator = DesiredConfigurationValidator(resolver)
    definer = DefineDesiredConfiguration(validator, configurations, desired)
    provisioner = ProvisionServer(
        resolver, configurations, desired, intents, operations, validator
    )
    tracker = TrackOperation(resolver, configurations, operations)
    configuration_id = target.configuration.configuration_id
    name = target.run_name
    seen: list[str] = []

    defined = definer.define_desired_configuration(
        configuration_id, name, target.image, [], None, target.values
    )
    assert defined.valid, defined.problems
    checked = definer.validate_desired_configuration(configuration_id, name)
    assert checked.valid, checked.problems

    before = len(writes_sent)
    preview = provisioner.preview_provisioning(configuration_id, name, "")
    seen.append(repr(preview))
    assert preview.outcome is ProvisionOutcome.READY, preview.reason
    assert not preview.cost_consequence
    assert preview.server_name == name
    assert len(writes_sent) == before

    result = provisioner.request_provisioning(
        configuration_id, name, "", Confirmation(given=True)
    )
    seen.append(repr(result))
    if result.server_identifier:
        target.cleanup.known_vmids.add(result.server_identifier)
    assert result.outcome is ProvisionOutcome.REQUESTED, result.reason
    assert result.operation is not None
    assert result.server_identifier is not None

    ended = tracker.follow_operation(result.operation.operation_id)
    seen.extend((repr(ended), ended.detail or ""))
    assert ended.state is OperationState.SUCCEEDED, ended.detail
    completed = provisioner.complete_provisioning(result.operation.operation_id)
    seen.append(repr(completed))
    assert completed.outcome is ProvisionOutcome.COMPLETED, completed.reason

    recorded = configurations.managed_servers(configuration_id)
    assert [r.server_identifier for r in recorded] == [result.server_identifier]
    listed = target.provider.list_servers(target.configuration)
    assert [s.server_identifier for s in listed if s.name == name] == [
        result.server_identifier
    ]

    before = len(writes_sent)
    again = provisioner.request_provisioning(
        configuration_id, name, "", Confirmation(given=True)
    )
    seen.append(repr(again))
    assert again.outcome is ProvisionOutcome.NAME_EXISTS, again.outcome
    assert len(writes_sent) == before

    seen.append(target.store_path.read_text(encoding="utf-8"))
    leaked = any(target.leaks(text) for text in seen)
    assert not leaked, "a stored or reported text contains a credential value"
