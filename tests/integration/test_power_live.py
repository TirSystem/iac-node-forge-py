"""Live power tests: they TURN A REAL SERVER OFF AND ON.

Each provider is opted in on its own, and nothing runs unless every variable
of that provider is set. Run them only against a disposable server.

Proxmox VE (``-k "power and proxmox"``):
``PVE_TOKEN`` (full ``USER@REALM!TOKENID=SECRET``), optional ``PVE_ADDRESS``
and ``PVE_VERIFY`` as in ``test_proxmox_live.py``, ``PVE_POWER_TEST_SERVER``
(the vmid of the disposable server) and ``PVE_POWER_TEST_CONFIRM``, which must
equal ``turn-off-server-<vmid>``.

Contabo (``-k "power and contabo"``): the four ``CONTABO_`` variables of
``test_contabo_live.py``, optional ``CONTABO_API_URL``,
``CONTABO_POWER_TEST_SERVER`` (the instance id) and
``CONTABO_POWER_TEST_CONFIRM``, which must equal
``turn-off-server-<instance id>``.

The hard power off (``stop``) can lose unsaved data and is a separate test that
also needs ``POWER_TEST_ALLOW_HARD_STOP=1``.

The scenario changes only the power state of the one named server, and puts the
server back into its original state even when an assertion fails. No
credential value and no confirmation text is printed, logged or put in an
assertion message: checks that involve a value are reduced to a boolean.
"""

import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from iac_node_forge.application.dto import (
    Confirmation,
    OperationSummary,
    PowerActionResult,
    PowerOutcome,
)
from iac_node_forge.application.errors import ProviderError
from iac_node_forge.application.ports import (
    ConfigurationStore,
    ProviderPort,
    ServerPowerPort,
    ServerProvisionPort,
)
from iac_node_forge.application.use_cases.control_server_power import (
    ControlServerPower,
)
from iac_node_forge.application.use_cases.track_operation import TrackOperation
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.operation import OperationId, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.infrastructure.configuration import JsonLinesOperationStore
from iac_node_forge.infrastructure.contabo import ContaboProvider
from iac_node_forge.infrastructure.proxmox import ProxmoxProvider

pytestmark = pytest.mark.integration

type Scenario = Callable[[str, list[str]], None]
"""Takes the original state and a list that collects texts to leak-check."""

DEFAULT_PVE_ADDRESS = "https://localhost:8006"
DEFAULT_CONTABO_URL = "https://api.contabo.com"
CONFIRM_PREFIX = "turn-off-server-"
HARD_STOP_VARIABLE = "POWER_TEST_ALLOW_HARD_STOP"
CONFIGURATION_ID = ConfigurationId("live-power")
RUNNING = "running"
STOPPED = "stopped"
STATE_WAIT_SECONDS = 60.0
STATE_POLL_SECONDS = 2.0
TIMEOUT_SECONDS = 20.0
SECRET_SEPARATOR = "="
CONTABO_VARIABLES = {
    "client_id": "CONTABO_CLIENT_ID",
    "client_secret": "CONTABO_CLIENT_SECRET",
    "api_user": "CONTABO_API_USER",
    "api_password": "CONTABO_API_PASSWORD",
}


class _SingleConfigurationStore:
    """ConfigurationStore of the one live configuration; no server is managed."""

    def __init__(self, configuration: ProviderConfiguration) -> None:
        self._configuration = configuration

    def save(self, configuration: ProviderConfiguration) -> None:
        raise AssertionError("a live power test never saves a configuration")

    def load_all(self) -> tuple[ProviderConfiguration, ...]:
        return (self._configuration,)

    def managed_servers(
        self, configuration_id: ConfigurationId
    ) -> tuple[ManagedServerRecord, ...]:
        return ()

    def save_managed_server(self, record: ManagedServerRecord) -> None:
        raise AssertionError("a live power test never records a managed server")


class _FixedResolver:
    """ProviderResolver that hands out the one real adapter for both ports."""

    def __init__(self, provider: ProxmoxProvider | ContaboProvider) -> None:
        self._provider = provider

    def provider_for(self, configuration: ProviderConfiguration) -> ProviderPort:
        return self._provider

    def power_for(self, configuration: ProviderConfiguration) -> ServerPowerPort:
        return self._provider

    def provision_for(
        self, configuration: ProviderConfiguration
    ) -> ServerProvisionPort:
        return self._provider


@dataclass(frozen=True, slots=True, repr=False)
class PowerTarget:
    """The real server to act on, with the use cases wired to its adapter.

    It hides its credential values from any output.
    """

    provider: ProxmoxProvider | ContaboProvider
    configuration: ProviderConfiguration
    server: str
    control: ControlServerPower
    tracker: TrackOperation
    store: JsonLinesOperationStore
    secrets: tuple[str, ...] = field(repr=False)

    def __repr__(self) -> str:
        return f"PowerTarget(server={self.server!r})"

    def leaks(self, text: str) -> bool:
        """Say whether ``text`` contains any credential value."""
        return any(secret in text for secret in self.secrets)


def _skip_unless_set(names: list[str], who: str) -> None:
    missing = [name for name in names if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip(
            f"no disposable {who} to power-test; not set: " + ", ".join(missing)
        )


def _skip_unless_confirmed(server_variable: str, confirm_variable: str) -> str:
    """Return the server identifier when the confirm text is exactly right."""
    server = os.environ[server_variable].strip()
    confirmed = os.environ[confirm_variable].strip() == CONFIRM_PREFIX + server
    if not confirmed:
        pytest.skip("confirmation text does not match")
    return server


def _build_target(
    provider: ProxmoxProvider | ContaboProvider,
    configuration: ProviderConfiguration,
    server: str,
    secrets: tuple[str, ...],
    directory: Path,
) -> PowerTarget:
    # A real adapter is required: a silently substituted double would pass.
    assert type(provider) in (ProxmoxProvider, ContaboProvider)
    store = JsonLinesOperationStore(directory / "operations.jsonl")
    resolver = _FixedResolver(provider)
    configurations: ConfigurationStore = _SingleConfigurationStore(configuration)
    return PowerTarget(
        provider,
        configuration,
        server,
        ControlServerPower(resolver, configurations, store),
        TrackOperation(resolver, configurations, store),
        store,
        tuple(secret for secret in secrets if secret),
    )


def _verify_from_environment() -> bool | str:
    value = os.environ.get("PVE_VERIFY", "true").strip()
    lowered = value.lower()
    if lowered in ("", "true"):
        return True
    if lowered == "false":
        return False
    return value


@pytest.fixture(scope="module")
def proxmox_target(tmp_path_factory: pytest.TempPathFactory) -> PowerTarget:
    names = ["PVE_TOKEN", "PVE_POWER_TEST_SERVER", "PVE_POWER_TEST_CONFIRM"]
    _skip_unless_set(names, "Proxmox VE server")
    server = _skip_unless_confirmed("PVE_POWER_TEST_SERVER", "PVE_POWER_TEST_CONFIRM")
    token = os.environ["PVE_TOKEN"].strip()
    address = os.environ.get("PVE_ADDRESS", "").strip() or DEFAULT_PVE_ADDRESS
    configuration = ProviderConfiguration(
        CONFIGURATION_ID, ProviderKind.PROXMOX_VE, "Live power Proxmox VE", address
    )
    provider = ProxmoxProvider(
        Credentials.single(token),
        verify=_verify_from_environment(),
        timeout=TIMEOUT_SECONDS,
    )
    _, _, secret = token.partition(SECRET_SEPARATOR)
    return _build_target(
        provider,
        configuration,
        server,
        (token, secret),
        tmp_path_factory.mktemp("power-proxmox"),
    )


@pytest.fixture(scope="module")
def contabo_target(tmp_path_factory: pytest.TempPathFactory) -> PowerTarget:
    names = [
        *CONTABO_VARIABLES.values(),
        "CONTABO_POWER_TEST_SERVER",
        "CONTABO_POWER_TEST_CONFIRM",
    ]
    _skip_unless_set(names, "Contabo instance")
    server = _skip_unless_confirmed(
        "CONTABO_POWER_TEST_SERVER", "CONTABO_POWER_TEST_CONFIRM"
    )
    values = {
        name: os.environ[variable].strip()
        for name, variable in CONTABO_VARIABLES.items()
    }
    address = os.environ.get("CONTABO_API_URL", "").strip() or DEFAULT_CONTABO_URL
    configuration = ProviderConfiguration(
        CONFIGURATION_ID, ProviderKind.CONTABO, "Live power Contabo", address
    )
    provider = ContaboProvider(Credentials(dict(values)), timeout=TIMEOUT_SECONDS)
    return _build_target(
        provider,
        configuration,
        server,
        tuple(values.values()),
        tmp_path_factory.mktemp("power-contabo"),
    )


@pytest.fixture
def writes_sent(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Record every request that is neither a read nor a token request."""
    writes: list[str] = []
    original = httpx.Client.send

    def recording_send(
        self: httpx.Client, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        is_read = request.method in ("GET", "HEAD")
        is_token = "token" in request.url.path
        if not (is_read or is_token):
            writes.append(request.method)
        return original(self, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", recording_send)
    yield writes


def _guard_identity(target: PowerTarget) -> None:
    """Refuse to go on when the configured name resolves to another server."""
    found = target.provider.get_server(target.configuration, target.server)
    if found.server_identifier != target.server:
        pytest.fail("the configured server resolves to a different server")


def _state_of(target: PowerTarget) -> str:
    server = target.provider.get_server(target.configuration, target.server)
    return server.state.strip().casefold()


def _wait_for_state(target: PowerTarget, wanted: str) -> str:
    deadline = time.monotonic() + STATE_WAIT_SECONDS
    state = _state_of(target)
    while state != wanted and time.monotonic() < deadline:
        time.sleep(STATE_POLL_SECONDS)
        state = _state_of(target)
    return state


def _request(target: PowerTarget, action: PowerAction) -> PowerActionResult:
    return target.control.request_power_action(
        target.configuration.configuration_id,
        target.server,
        action,
        Confirmation(given=True, typed_identifier=target.server),
    )


def _act(
    target: PowerTarget, action: PowerAction, state_after: str, seen: list[str]
) -> None:
    """Request ``action``, follow it and check its record and the new state."""
    result = _request(target, action)
    seen.append(repr(result))
    assert result.outcome is PowerOutcome.REQUESTED, result.outcome
    assert result.operation is not None
    assert result.server_identifier == target.server
    assert result.operation.server_identifier == target.server
    ended = target.tracker.follow_operation(result.operation.operation_id)
    seen.extend((repr(ended), ended.detail or ""))
    assert ended.state is OperationState.SUCCEEDED, ended.state
    assert ended.server_identifier == target.server
    recorded = target.store.load(OperationId(ended.operation_id))
    seen.append(repr(recorded))
    assert OperationSummary.of(recorded) == ended
    assert recorded.state is OperationState.SUCCEEDED
    assert recorded.finished_at is not None
    assert _wait_for_state(target, state_after) == state_after


def _check_already_running(
    target: PowerTarget, writes: list[str], seen: list[str]
) -> None:
    before = len(writes)
    result = _request(target, PowerAction.START)
    seen.append(repr(result))
    assert result.outcome is PowerOutcome.ALREADY_IN_STATE, result.outcome
    assert result.operation is None
    assert len(writes) == before


def _restore(target: PowerTarget, original: str) -> None:
    """Put the server back into its original power state."""
    if _state_of(target) == original:
        return
    action = PowerAction.START if original == RUNNING else PowerAction.SHUTDOWN
    result = _request(target, action)
    if result.operation is not None:
        target.tracker.follow_operation(result.operation.operation_id)
    if _wait_for_state(target, original) == original or original != STOPPED:
        return
    if _hard_stop_allowed():
        # A graceful shutdown that the guest never answers must not leave the
        # server running; the opt-in already allows a hard power off.
        forced = _request(target, PowerAction.STOP)
        if forced.operation is not None:
            target.tracker.follow_operation(forced.operation.operation_id)
        _wait_for_state(target, original)


def _run_guarded(target: PowerTarget, scenario: Scenario) -> None:
    """Run ``scenario``; restore the original state; check for leaks."""
    _guard_identity(target)
    original = _state_of(target)
    if original not in (RUNNING, STOPPED):
        pytest.fail(f"the server is neither running nor stopped: {original!r}")
    seen: list[str] = []
    try:
        scenario(original, seen)
    except ProviderError as error:
        if target.leaks(str(error)):
            pytest.fail("a provider error text contains a credential value")
        raise
    finally:
        _restore(target, original)
    leaked = any(target.leaks(text) for text in seen)
    assert not leaked, "an operation text contains a credential value"
    assert _state_of(target) == original


def _cycle(target: PowerTarget, writes: list[str]) -> Scenario:
    def scenario(original: str, seen: list[str]) -> None:
        if original == RUNNING:
            _check_already_running(target, writes, seen)
            _act(target, PowerAction.SHUTDOWN, STOPPED, seen)
            _act(target, PowerAction.START, RUNNING, seen)
            _act(target, PowerAction.RESTART, RUNNING, seen)
        else:
            _act(target, PowerAction.START, RUNNING, seen)
            _check_already_running(target, writes, seen)

    return scenario


def _hard_stop(target: PowerTarget) -> Scenario:
    def scenario(original: str, seen: list[str]) -> None:
        if original != RUNNING:
            _act(target, PowerAction.START, RUNNING, seen)
        _act(target, PowerAction.STOP, STOPPED, seen)

    return scenario


def _hard_stop_allowed() -> bool:
    return os.environ.get(HARD_STOP_VARIABLE, "").strip() == "1"


def _require_hard_stop_allowed() -> None:
    if not _hard_stop_allowed():
        pytest.skip(f"hard power off is not allowed; {HARD_STOP_VARIABLE} is not 1")


def test_power_proxmox_cycle_restores_original_state(
    proxmox_target: PowerTarget, writes_sent: list[str]
) -> None:
    _run_guarded(proxmox_target, _cycle(proxmox_target, writes_sent))


def test_power_proxmox_hard_stop_needs_extra_opt_in(
    proxmox_target: PowerTarget,
) -> None:
    _require_hard_stop_allowed()

    _run_guarded(proxmox_target, _hard_stop(proxmox_target))


def test_power_contabo_cycle_restores_original_state(
    contabo_target: PowerTarget, writes_sent: list[str]
) -> None:
    _run_guarded(contabo_target, _cycle(contabo_target, writes_sent))


def test_power_contabo_hard_stop_needs_extra_opt_in(
    contabo_target: PowerTarget,
) -> None:
    _require_hard_stop_allowed()

    _run_guarded(contabo_target, _hard_stop(contabo_target))
