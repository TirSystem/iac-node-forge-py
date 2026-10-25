"""What the live Proxmox VE tests share: the opt-in, the target and the product calls.

Used by ``test_provision_live.py`` and ``test_full_backup_live.py``. Only
Proxmox VE is tested live; Contabo is never created, backed up or restored
live. Nothing here prints or stores a credential value.
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
TEST_ONLY_HEADER = "X-Iacnf-Test-Only"
"""Marks a request a test sends itself, so it is counted apart from the product's."""


def verify_from_environment() -> bool | str:
    value = os.environ.get("PVE_VERIFY", "true").strip()
    lowered = value.lower()
    if lowered in ("", "true"):
        return True
    if lowered == "false":
        return False
    return value


class FixedResolver:
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


def remover(target: ProvisionTarget) -> RemoveServer:
    """Build the product's removal use case over the real adapter."""
    return RemoveServer(
        FixedResolver(target.provider),
        JsonLinesConfigurationStore(target.store_path),
        JsonLinesOperationStore(target.store_path),
    )


def tracker_of(target: ProvisionTarget) -> TrackOperation:
    """Build the product's operation tracking over the real adapter."""
    return TrackOperation(
        FixedResolver(target.provider),
        JsonLinesConfigurationStore(target.store_path),
        JsonLinesOperationStore(target.store_path),
    )


def remove_leftover(target: ProvisionTarget) -> None:
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
    removal = remover(target)
    tracker = tracker_of(target)
    configuration_id = target.configuration.configuration_id
    confirmation = Confirmation(given=True, typed_identifier=identifier)
    # A create task that is still running locks the container: ask again.
    deadline = time.monotonic() + REMOVAL_RETRY_SECONDS
    while True:
        result = removal.request_removal(configuration_id, identifier, confirmation)
        if result.outcome is RemovalOutcome.REQUESTED and result.operation:
            tracker.follow_operation(result.operation.operation_id)
            removal.complete_removal(result.operation.operation_id)
            return
        if result.outcome is not RemovalOutcome.REJECTED or (
            time.monotonic() >= deadline
        ):
            break
        time.sleep(REMOVAL_POLL_SECONDS)
    pytest.fail(f"container {identifier} ({target.run_name}) could not be removed")


def skip_unless_ready() -> None:
    missing = [name for name in VARIABLES if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip("no live provisioning opt-in; not set: " + ", ".join(missing))
    if os.environ["PVE_PROVISION_TEST_CONFIRM"].strip() != CONFIRM_TEXT:
        pytest.skip("confirmation text does not match")


def option_value(node: str, name: str) -> str:
    """The option value the adapter lists: ``<node>/<name>``."""
    return name if name.startswith(f"{node}/") else f"{node}/{name}"


def live_target(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ProvisionTarget]:
    """Yield the target of a module; remove this run's container at the end."""
    skip_unless_ready()
    token = os.environ["PVE_TOKEN"].strip()
    node = os.environ["PVE_PROVISION_TEST_NODE"].strip()
    storage = os.environ["PVE_PROVISION_TEST_STORAGE"].strip()
    template = os.environ["PVE_PROVISION_TEST_TEMPLATE"].strip()
    address = os.environ.get("PVE_ADDRESS", "").strip() or DEFAULT_PVE_ADDRESS
    verify = verify_from_environment()
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
            "storage": option_value(node, storage),
            "memory_mb": "128",
            "cores": "1",
            "disk_gb": "1",
        },
        option_value(node, template),
        tuple(text for text in (token, secret) if text),
    )
    try:
        yield live
    finally:
        remove_leftover(live)


@dataclass(slots=True)
class WriteLog:
    """The requests that are neither a read nor a token request.

    ``product`` holds the method of each write the product sent. ``everything``
    holds every write in order; one a test sent itself (marked with
    ``TEST_ONLY_HEADER``) shows as ``test-only <METHOD>``.
    """

    product: list[str] = field(default_factory=list)
    everything: list[str] = field(default_factory=list)


def record_writes(monkeypatch: pytest.MonkeyPatch) -> Iterator[WriteLog]:
    """Record every write sent through ``httpx.Client`` while the log is in use."""
    log = WriteLog()
    original = httpx.Client.send

    def recording_send(
        self: httpx.Client, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        if request.method not in ("GET", "HEAD") and "token" not in request.url.path:
            if TEST_ONLY_HEADER in request.headers:
                log.everything.append(f"test-only {request.method}")
            else:
                log.product.append(request.method)
                log.everything.append(request.method)
        return original(self, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", recording_send)
    yield log


def create_container(
    target: ProvisionTarget, writes_sent: list[str], seen: list[str]
) -> tuple[str, ProvisionServer]:
    """Create this run's container through the product; return its id.

    Defines and validates the description, previews (no write), requests with a
    plain confirmation, tracks and completes. The provisioner is returned for
    callers that ask again.
    """
    resolver = FixedResolver(target.provider)
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
    return result.server_identifier, provisioner


def remove_through_product(
    target: ProvisionTarget, writes_sent: list[str], identifier: str
) -> list[str]:
    """Remove the created container with the product; return texts to scan."""
    resolver = FixedResolver(target.provider)
    configurations = JsonLinesConfigurationStore(target.store_path)
    operations = JsonLinesOperationStore(target.store_path)
    removal = remover(target)
    tracker = TrackOperation(resolver, configurations, operations)
    configuration_id = target.configuration.configuration_id
    seen: list[str] = []

    preview = removal.preview_removal(configuration_id, identifier)
    seen.append(repr(preview))
    assert preview.outcome is RemovalOutcome.READY, preview.reason
    assert preview.server_state == STOPPED
    assert preview.managed
    assert "destroys" in preview.consequence
    assert "disks" in preview.consequence

    before = len(writes_sent)
    wrong = removal.request_removal(
        configuration_id, identifier, Confirmation(True, identifier + "0")
    )
    assert wrong.outcome is RemovalOutcome.IDENTIFIER_MISMATCH
    unconfirmed = removal.request_removal(
        configuration_id, identifier, Confirmation(given=False)
    )
    assert unconfirmed.outcome is RemovalOutcome.NOT_CONFIRMED
    assert len(writes_sent) == before

    requested = removal.request_removal(
        configuration_id, identifier, Confirmation(True, identifier)
    )
    seen.append(repr(requested))
    assert requested.outcome is RemovalOutcome.REQUESTED, requested.reason
    assert requested.operation is not None
    operation_id = requested.operation.operation_id
    ended = tracker.follow_operation(operation_id)
    seen.extend((repr(ended), ended.detail or ""))
    assert ended.state is OperationState.SUCCEEDED, ended.detail
    completed = removal.complete_removal(operation_id)
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
    gone = removal.preview_removal(configuration_id, identifier)
    assert gone.outcome is RemovalOutcome.NOT_FOUND
    again = removal.request_removal(
        configuration_id, identifier, Confirmation(True, identifier)
    )
    assert again.outcome is RemovalOutcome.NOT_FOUND
    assert len(writes_sent) == before
    return seen
