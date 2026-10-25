"""Live provisioning and removal test: it CREATES A REAL CONTAINER, then removes it.

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

The container is named ``iacnf-live-<8 hex>``, new on every run. The test
removes it through the product (``RemoveServer``: preview, request with the
typed identifier, track, complete). When the test fails before that, a
finalizer removes this run's container the same way, and only that one; the
product does not stop a server, so a running one is reported by id for removal
by hand. The test sends no raw delete. No credential value is printed, logged
or put in an assertion message.
"""

import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from iac_node_forge.application.dto import (
    Confirmation,
    ProvisionOutcome,
    RemovalOutcome,
)
from iac_node_forge.application.ports import (
    ProviderPort,
    ServerBackupPort,
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
from iac_node_forge.application.use_cases.remove_server import RemoveServer
from iac_node_forge.application.use_cases.track_operation import TrackOperation
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.operation import OperationId, OperationState
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
SECRET_SEPARATOR = "="
STOPPED = "stopped"
REMOVAL_RETRY_SECONDS = 120.0
REMOVAL_POLL_SECONDS = 2.0


def _verify_from_environment() -> bool | str:
    value = os.environ.get("PVE_VERIFY", "true").strip()
    lowered = value.lower()
    if lowered in ("", "true"):
        return True
    if lowered == "false":
        return False
    return value


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

    def backup_for(self, configuration: ProviderConfiguration) -> ServerBackupPort:
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
    secrets: tuple[str, ...] = field(repr=False)

    def __repr__(self) -> str:
        return f"ProvisionTarget(name={self.run_name!r})"

    def leaks(self, text: str) -> bool:
        """Say whether ``text`` contains any credential value."""
        return any(secret in text for secret in self.secrets)

    @property
    def store_path(self) -> Path:
        return self.directory / "configuration.jsonl"


def _remover(target: ProvisionTarget) -> RemoveServer:
    """Build the product's removal use case over the real adapter."""
    return RemoveServer(
        _FixedResolver(target.provider),
        JsonLinesConfigurationStore(target.store_path),
        JsonLinesOperationStore(target.store_path),
    )


def _remove_leftover(target: ProvisionTarget) -> None:
    """Safety net: remove this run's container, if left, through the product.

    It looks only for the name of this run (it starts with ``iacnf-live-``) and
    never stops a server: a running one is reported by id for removal by hand.
    """
    assert target.run_name.startswith(NAME_PREFIX)
    left = [
        server
        for server in target.provider.list_servers(target.configuration)
        if server.name == target.run_name
    ]
    if not left:
        return
    identifier = left[0].server_identifier
    if left[0].state != STOPPED:
        pytest.fail(
            f"container {identifier} ({target.run_name}) is not stopped; "
            "remove it by hand"
        )
    remover = _remover(target)
    tracker = TrackOperation(
        _FixedResolver(target.provider),
        JsonLinesConfigurationStore(target.store_path),
        JsonLinesOperationStore(target.store_path),
    )
    configuration_id = target.configuration.configuration_id
    confirmation = Confirmation(given=True, typed_identifier=identifier)
    # A create task that is still running locks the container: ask again.
    deadline = time.monotonic() + REMOVAL_RETRY_SECONDS
    while True:
        result = remover.request_removal(configuration_id, identifier, confirmation)
        if result.outcome is RemovalOutcome.REQUESTED and result.operation:
            tracker.follow_operation(result.operation.operation_id)
            remover.complete_removal(result.operation.operation_id)
            return
        if result.outcome is not RemovalOutcome.REJECTED or (
            time.monotonic() >= deadline
        ):
            break
        time.sleep(REMOVAL_POLL_SECONDS)
    pytest.fail(f"container {identifier} ({target.run_name}) could not be removed")


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
        tuple(text for text in (token, secret) if text),
    )
    try:
        yield live
    finally:
        _remove_leftover(live)


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


def _remove_through_product(
    target: ProvisionTarget, writes_sent: list[str], identifier: str
) -> list[str]:
    """Remove the created container with the product; return texts to scan."""
    resolver = _FixedResolver(target.provider)
    configurations = JsonLinesConfigurationStore(target.store_path)
    operations = JsonLinesOperationStore(target.store_path)
    remover = _remover(target)
    tracker = TrackOperation(resolver, configurations, operations)
    configuration_id = target.configuration.configuration_id
    seen: list[str] = []

    preview = remover.preview_removal(configuration_id, identifier)
    seen.append(repr(preview))
    assert preview.outcome is RemovalOutcome.READY, preview.reason
    assert preview.server_state == STOPPED
    assert preview.managed
    assert "destroys" in preview.consequence
    assert "disks" in preview.consequence

    before = len(writes_sent)
    wrong = remover.request_removal(
        configuration_id, identifier, Confirmation(True, identifier + "0")
    )
    assert wrong.outcome is RemovalOutcome.IDENTIFIER_MISMATCH
    unconfirmed = remover.request_removal(
        configuration_id, identifier, Confirmation(given=False)
    )
    assert unconfirmed.outcome is RemovalOutcome.NOT_CONFIRMED
    assert len(writes_sent) == before

    requested = remover.request_removal(
        configuration_id, identifier, Confirmation(True, identifier)
    )
    seen.append(repr(requested))
    assert requested.outcome is RemovalOutcome.REQUESTED, requested.reason
    assert requested.operation is not None
    operation_id = requested.operation.operation_id
    ended = tracker.follow_operation(operation_id)
    seen.extend((repr(ended), ended.detail or ""))
    assert ended.state is OperationState.SUCCEEDED, ended.detail
    completed = remover.complete_removal(operation_id)
    seen.append(repr(completed))
    assert completed.outcome is RemovalOutcome.COMPLETED, completed.reason
    assert completed.managed_ended

    recorded = operations.load(OperationId(operation_id))
    assert recorded.consequence == preview.consequence
    listed = target.provider.list_servers(target.configuration)
    assert identifier not in [s.server_identifier for s in listed]
    managed = configurations.managed_servers(configuration_id)
    assert identifier not in [r.server_identifier for r in managed]

    before = len(writes_sent)
    gone = remover.preview_removal(configuration_id, identifier)
    assert gone.outcome is RemovalOutcome.NOT_FOUND
    again = remover.request_removal(
        configuration_id, identifier, Confirmation(True, identifier)
    )
    assert again.outcome is RemovalOutcome.NOT_FOUND
    assert len(writes_sent) == before
    return seen


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

    seen.extend(_remove_through_product(target, writes_sent, result.server_identifier))
    assert writes_sent == ["POST", "DELETE"]

    seen.append(target.store_path.read_text(encoding="utf-8"))
    leaked = any(target.leaks(text) for text in seen)
    assert not leaked, "a stored or reported text contains a credential value"
