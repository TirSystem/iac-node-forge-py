"""In-memory fakes of the application ports, for tests."""

import uuid
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from iac_node_forge.application.capabilities import (
    INSPECT_SERVER,
    LIST_SERVER_OPTIONS,
    LIST_SERVERS,
    POWER_CAPABILITIES,
    PROVISION_SERVER,
    REMOVE_SERVER,
)
from iac_node_forge.application.errors import (
    CredentialEntryInvalidError,
    CredentialNotFoundError,
    CredentialStoreUnavailableError,
    OperationNotFoundError,
    PowerActionUnsupportedError,
    ProviderError,
    ProvisionIntentNotFoundError,
    ProvisionUnsupportedError,
    RemovalUnsupportedError,
    ServerNotFoundError,
    UnknownOptionKindError,
    UnsupportedProviderError,
)
from iac_node_forge.application.ports import (
    ProviderPort,
    RemovalBlock,
    ServerPowerPort,
    ServerProvisionPort,
    ServerRemovalPort,
)
from iac_node_forge.domain.access_result import AccessResult
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.operation import (
    Operation,
    OperationId,
    OperationState,
)
from iac_node_forge.domain.power_action import POWER_ACTIONS, PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.provision_intent import (
    IntentId,
    IntentState,
    ProvisionIntent,
)
from iac_node_forge.domain.removal_kind import RemovalKind
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_option import ServerOption
from iac_node_forge.domain.value_rule import ValueRule

DEFAULT_OPTION_KINDS = frozenset({"node", "storage"})


class FakeProvider:
    """Fake ProviderPort serving preset data."""

    def __init__(
        self,
        accepted_secret: str = "secret",
        capabilities: frozenset[Capability] = frozenset(),
        options: tuple[ServerOption, ...] = (),
        servers: tuple[Server, ...] = (),
    ) -> None:
        self._accepted_secret = accepted_secret
        self._capabilities = capabilities
        self._options = options
        self._option_kinds = DEFAULT_OPTION_KINDS | {o.kind for o in options}
        self._servers = servers

    def validate_access(self, address: str, credentials: Credentials) -> AccessResult:
        if self._accepted_secret in credentials.values.values():
            return AccessResult(succeeded=True)
        return AccessResult(succeeded=False, reason="credentials rejected")

    def discover_capabilities(
        self, configuration: ProviderConfiguration
    ) -> frozenset[Capability]:
        return self._capabilities

    def list_server_options(
        self, configuration: ProviderConfiguration, option_kind: str
    ) -> tuple[ServerOption, ...]:
        if option_kind not in self._option_kinds:
            raise UnknownOptionKindError(f"no options of kind {option_kind!r}")
        return tuple(o for o in self._options if o.kind == option_kind)

    def list_servers(self, configuration: ProviderConfiguration) -> tuple[Server, ...]:
        return self._servers

    def get_server(
        self, configuration: ProviderConfiguration, server_identifier: str
    ) -> Server:
        for server in self._servers:
            if server.server_identifier == server_identifier:
                return server
        raise ServerNotFoundError(server_identifier)


class FakeConfigurationStore:
    """Fake ConfigurationStore keeping data in memory."""

    def __init__(self, managed: tuple[ManagedServerRecord, ...] = ()) -> None:
        self._configurations: dict[ConfigurationId, ProviderConfiguration] = {}
        self.managed = managed

    def save(self, configuration: ProviderConfiguration) -> None:
        self._configurations[configuration.configuration_id] = configuration

    def load_all(self) -> tuple[ProviderConfiguration, ...]:
        return tuple(self._configurations.values())

    def managed_servers(
        self, configuration_id: ConfigurationId
    ) -> tuple[ManagedServerRecord, ...]:
        return tuple(r for r in self.managed if r.configuration_id == configuration_id)

    def save_managed_server(self, record: ManagedServerRecord) -> None:
        key = (record.configuration_id, record.server_identifier)
        if key not in {(r.configuration_id, r.server_identifier) for r in self.managed}:
            self.managed = (*self.managed, record)


class FakeCredentialStore:
    """Fake CredentialStore keeping secrets in memory.

    With ``unavailable`` set it behaves like a host without a keyring.
    """

    def __init__(self, unavailable: bool = False) -> None:
        self.secrets: dict[CredentialReference, Credentials] = {}
        self.unavailable = unavailable
        # References whose entry exists but cannot be read, as a corrupt keyring
        # entry would be.
        self.invalid: set[CredentialReference] = set()

    def put(self, reference: CredentialReference, credentials: Credentials) -> None:
        if self.unavailable:
            raise CredentialStoreUnavailableError("no keyring")
        self.secrets[reference] = credentials

    def get(self, reference: CredentialReference) -> Credentials:
        if self.unavailable:
            raise CredentialStoreUnavailableError("no keyring")
        if reference in self.invalid:
            raise CredentialEntryInvalidError(f"entry {reference.name!r} unreadable")
        if reference not in self.secrets:
            raise CredentialNotFoundError(reference.name)
        return self.secrets[reference]


class FakeCredentialPrompt:
    """Fake CredentialPrompt answering with a preset secret."""

    def __init__(self, secret: str = "secret") -> None:
        self.secret = secret
        self.asked: list[ProviderKind] = []

    def ask(self, provider_kind: ProviderKind) -> Credentials:
        self.asked.append(provider_kind)
        return Credentials({n: self.secret for n in provider_kind.credential_names})


class FailingProvider(FakeProvider):
    """A provider whose read operations raise the given error."""

    def __init__(self, error: ProviderError) -> None:
        super().__init__()
        self._error = error

    def discover_capabilities(
        self, configuration: ProviderConfiguration
    ) -> frozenset[Capability]:
        raise self._error

    def list_server_options(
        self, configuration: ProviderConfiguration, option_kind: str
    ) -> tuple[ServerOption, ...]:
        raise self._error

    def list_servers(self, configuration: ProviderConfiguration) -> tuple[Server, ...]:
        raise self._error

    def get_server(
        self, configuration: ProviderConfiguration, server_identifier: str
    ) -> Server:
        raise self._error


class FakeProviderResolver:
    """Fake ProviderResolver: one provider per configuration id, or one for all.

    The power port is ``power`` (or ``power_by_configuration``) when given;
    otherwise the provider itself when it also implements ServerPowerPort.
    """

    def __init__(
        self,
        default: ProviderPort,
        by_configuration: dict[ConfigurationId, ProviderPort] | None = None,
        power: ServerPowerPort | None = None,
        power_by_configuration: dict[ConfigurationId, ServerPowerPort] | None = None,
        provision: ServerProvisionPort | None = None,
        provision_by_configuration: dict[ConfigurationId, ServerProvisionPort]
        | None = None,
        removal: ServerRemovalPort | None = None,
        removal_by_configuration: dict[ConfigurationId, ServerRemovalPort]
        | None = None,
    ) -> None:
        self._default = default
        self._by_configuration = by_configuration or {}
        self._power = power
        self._power_by_configuration = power_by_configuration or {}
        self._provision = provision
        self._provision_by_configuration = provision_by_configuration or {}
        self._removal = removal
        self._removal_by_configuration = removal_by_configuration or {}
        self.resolved: list[ConfigurationId] = []
        self.power_resolved: list[ConfigurationId] = []
        self.provision_resolved: list[ConfigurationId] = []
        self.removal_resolved: list[ConfigurationId] = []

    def provider_for(self, configuration: ProviderConfiguration) -> ProviderPort:
        self.resolved.append(configuration.configuration_id)
        return self._by_configuration.get(configuration.configuration_id, self._default)

    def power_for(self, configuration: ProviderConfiguration) -> ServerPowerPort:
        self.power_resolved.append(configuration.configuration_id)
        key = configuration.configuration_id
        if key in self._power_by_configuration:
            return self._power_by_configuration[key]
        if self._power is not None:
            return self._power
        provider = self._by_configuration.get(key, self._default)
        if isinstance(provider, ServerPowerPort):
            return provider
        raise UnsupportedProviderError("the fake provider has no power actions")

    def provision_for(
        self, configuration: ProviderConfiguration
    ) -> ServerProvisionPort:
        self.provision_resolved.append(configuration.configuration_id)
        key = configuration.configuration_id
        if key in self._provision_by_configuration:
            return self._provision_by_configuration[key]
        if self._provision is not None:
            return self._provision
        provider = self._by_configuration.get(key, self._default)
        if isinstance(provider, ServerProvisionPort):
            return provider
        raise UnsupportedProviderError("the fake provider cannot create servers")

    def removal_for(self, configuration: ProviderConfiguration) -> ServerRemovalPort:
        self.removal_resolved.append(configuration.configuration_id)
        key = configuration.configuration_id
        if key in self._removal_by_configuration:
            return self._removal_by_configuration[key]
        if self._removal is not None:
            return self._removal
        provider = self._by_configuration.get(key, self._default)
        if isinstance(provider, ServerRemovalPort):
            return provider
        raise UnsupportedProviderError("the fake provider cannot remove servers")


class FakePowerProvider:
    """Fake ServerPowerPort with scripted behaviour and a record of requests.

    A request returns a RUNNING operation; each ``operation_state`` call is one
    step, and the operation ends after ``steps_to_finish`` steps. Call
    ``reject_with`` to make requests fail, ``make_operations_fail`` to make
    operations end FAILED. Every accepted request is in ``requests``.
    """

    def __init__(
        self,
        servers: Iterable[str] = ("100", "101"),
        supported: frozenset[PowerAction] = frozenset(POWER_ACTIONS),
        steps_to_finish: int = 2,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._servers = frozenset(servers)
        self._supported = supported
        self._steps_to_finish = steps_to_finish
        self._clock = clock
        self._rejection: ProviderError | None = None
        self._operations_fail = False
        self._steps: dict[OperationId, int] = {}
        self.requests: list[tuple[str, PowerAction]] = []

    def reject_with(self, error: ProviderError) -> None:
        self._rejection = error

    def make_operations_fail(self) -> None:
        self._operations_fail = True

    def request_power(
        self,
        configuration: ProviderConfiguration,
        server_identifier: str,
        action: PowerAction,
    ) -> Operation:
        if action not in self._supported:
            raise PowerActionUnsupportedError(f"{action.value} is not supported")
        if server_identifier not in self._servers:
            raise ServerNotFoundError(server_identifier)
        started_at = self._clock()
        rejected = self._rejection is not None
        operation = Operation(
            operation_id=OperationId(uuid.uuid4().hex),
            configuration_id=configuration.configuration_id,
            server_identifier=server_identifier,
            action=action,
            state=OperationState.RUNNING,
            started_at=started_at,
            provider_reference=None
            if rejected
            else f"fake-task-{len(self.requests) + 1}",
        )
        if self._rejection is not None:
            return operation.ended(
                OperationState.FAILED, started_at, str(self._rejection)
            )
        self.requests.append((server_identifier, action))
        self._steps[operation.operation_id] = 0
        return operation

    def operation_state(
        self, configuration: ProviderConfiguration, operation: Operation
    ) -> Operation:
        if operation.has_ended:
            return operation
        steps = self._steps.get(operation.operation_id, 0) + 1
        self._steps[operation.operation_id] = steps
        if steps < self._steps_to_finish:
            return operation
        finished_at = max(self._clock(), operation.started_at)
        if self._operations_fail:
            return operation.ended(
                OperationState.FAILED, finished_at, "the fake task failed"
            )
        return operation.ended(OperationState.SUCCEEDED, finished_at, None)


class FakeOperationStore:
    """Fake OperationStore keeping operations in memory."""

    def __init__(self) -> None:
        self.operations: dict[OperationId, Operation] = {}
        self.saved: list[Operation] = []

    def save(self, operation: Operation) -> None:
        self.operations[operation.operation_id] = operation
        self.saved.append(operation)

    def load(self, operation_id: OperationId) -> Operation:
        if operation_id not in self.operations:
            raise OperationNotFoundError(operation_id)
        return self.operations[operation_id]


class FakeDesiredConfigurationStore:
    """Fake DesiredConfigurationStore keeping desired configurations in memory."""

    def __init__(self) -> None:
        self.saved: dict[tuple[ConfigurationId, str], DesiredConfiguration] = {}
        self.saves = 0

    def save(self, desired: DesiredConfiguration) -> None:
        self.saves += 1
        self.saved[desired.configuration_id, desired.desired_name] = desired

    def load(
        self, configuration_id: ConfigurationId, desired_name: str
    ) -> DesiredConfiguration | None:
        return self.saved.get((configuration_id, desired_name))

    def load_all(
        self, configuration_id: ConfigurationId
    ) -> tuple[DesiredConfiguration, ...]:
        return tuple(d for (c, _), d in self.saved.items() if c == configuration_id)


class FakeProvisionIntentStore:
    """Fake ProvisionIntentStore; ``saved`` lists every save in order."""

    def __init__(self) -> None:
        self.intents: dict[IntentId, ProvisionIntent] = {}
        self.saved: list[ProvisionIntent] = []

    def save(self, intent: ProvisionIntent) -> None:
        self.intents[intent.intent_id] = intent
        self.saved.append(intent)

    def load(self, intent_id: IntentId) -> ProvisionIntent:
        if intent_id not in self.intents:
            raise ProvisionIntentNotFoundError(intent_id)
        return self.intents[intent_id]

    def find_pending(
        self, configuration_id: ConfigurationId, server_name: str
    ) -> ProvisionIntent | None:
        return next(
            (
                i
                for i in self.intents.values()
                if i.state is IntentState.PENDING
                and i.configuration_id == configuration_id
                and i.server_name == server_name
            ),
            None,
        )

    def load_by_operation(self, operation_id: OperationId) -> ProvisionIntent | None:
        return next(
            (i for i in self.intents.values() if i.operation_id == operation_id), None
        )


_STATE_AFTER: dict[PowerAction, str] = {
    PowerAction.START: "running",
    PowerAction.SHUTDOWN: "stopped",
    PowerAction.STOP: "stopped",
    PowerAction.RESTART: "running",
}

POWER_CAPABILITY_SET = frozenset(Capability(name) for name in POWER_CAPABILITIES)


class FakePoweredProvider(FakeProvider):
    """One fake that is both a ProviderPort and a ServerPowerPort.

    It is how a real adapter looks to the resolver. A succeeded operation
    changes the state the server is read with, as a real server would.
    """

    def __init__(
        self,
        servers: tuple[Server, ...] = (),
        supported: frozenset[PowerAction] = frozenset(POWER_ACTIONS),
        steps_to_finish: int = 2,
        extra_capabilities: frozenset[Capability] = frozenset(),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__(
            capabilities=POWER_CAPABILITY_SET | extra_capabilities, servers=servers
        )
        self.power = FakePowerProvider(
            servers=[server.server_identifier for server in servers],
            supported=supported,
            steps_to_finish=steps_to_finish,
            clock=clock,
        )

    def request_power(
        self,
        configuration: ProviderConfiguration,
        server_identifier: str,
        action: PowerAction,
    ) -> Operation:
        return self.power.request_power(configuration, server_identifier, action)

    def operation_state(
        self, configuration: ProviderConfiguration, operation: Operation
    ) -> Operation:
        updated = self.power.operation_state(configuration, operation)
        if updated.state is OperationState.SUCCEEDED and not operation.has_ended:
            self._servers = tuple(
                Server(
                    s.server_identifier,
                    s.name,
                    _STATE_AFTER[updated.action],
                    s.size,
                    s.addresses,
                    s.managed,
                )
                if s.server_identifier == updated.server_identifier
                else s
                for s in self._servers
            )
        return updated


class FakeClock:
    """A clock that only moves when ``sleep`` is called, and remembers the sleeps."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


FAKE_VALUE_RULES = (
    ValueRule("node", required=True, option_kind="node"),
    ValueRule("storage", required=True, option_kind="storage"),
    ValueRule("cores", required=True, minimum=1, maximum=64),
)
FAKE_FIRST_IDENTIFIER = 200


class FakeProvisionProvider(FakeProvider):
    """Fake ProviderPort and ServerProvisionPort with scripted behaviour.

    A request returns a RUNNING operation that names the new server; each
    ``operation_state`` call is one step, and the operation ends after
    ``steps_to_finish`` steps. A succeeded operation adds the server to
    ``list_servers``, as a real provider would. ``reserves`` tells whether the
    provider hands out an identifier before the request (Proxmox VE) or names
    the server itself (Contabo). Every accepted request is in ``requests``,
    as (server name, reserved identifier). ``supported`` False leaves the
    capability out, so a request is refused as unsupported.
    """

    def __init__(
        self,
        servers: tuple[Server, ...] = (),
        options: tuple[ServerOption, ...] = (),
        rules: tuple[ValueRule, ...] = FAKE_VALUE_RULES,
        supported: bool = True,
        reserves: bool = True,
        cost: str | None = None,
        steps_to_finish: int = 2,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        names = (LIST_SERVERS, LIST_SERVER_OPTIONS, INSPECT_SERVER)
        super().__init__(
            capabilities=frozenset(
                Capability(name)
                for name in (*names, *((PROVISION_SERVER,) if supported else ()))
            ),
            options=options,
            servers=servers,
        )
        self._rules = rules
        self._supported = supported
        self._reserves = reserves
        self._cost = cost
        self._steps_to_finish = steps_to_finish
        self._clock = clock
        self._rejection: ProviderError | None = None
        self._operations_fail = False
        self._answers_unknown = False
        self._next_identifier = FAKE_FIRST_IDENTIFIER
        self._steps: dict[OperationId, int] = {}
        self._creating: dict[OperationId, Server] = {}
        self.requests: list[tuple[str, str | None]] = []
        self.reads: list[str] = []
        # Called when a create request arrives, before the fake acts on it.
        self.on_request: Callable[[], None] | None = None

    def reject_with(self, error: ProviderError | None) -> None:
        """Make creations fail with ``error``; None makes them succeed again."""
        self._rejection = error

    def add_server(self, server: Server) -> None:
        """Add a server to the listing, as if it already existed."""
        self._servers = (*self._servers, server)

    def answer_unknown(self) -> None:
        """Make a creation end at once as UNKNOWN, as after a lost answer."""
        self._answers_unknown = True

    def make_operations_fail(self) -> None:
        self._operations_fail = True

    def value_rules(
        self, configuration: ProviderConfiguration
    ) -> tuple[ValueRule, ...]:
        self.reads.append("value_rules")
        return self._rules

    def reserve_identifier(self, configuration: ProviderConfiguration) -> str | None:
        self.reads.append("reserve_identifier")
        if not self._reserves:
            return None
        return str(self._take_identifier())

    def cost_consequence(
        self, configuration: ProviderConfiguration, desired: DesiredConfiguration
    ) -> str | None:
        self.reads.append("cost_consequence")
        return self._cost

    def request_provision(
        self,
        configuration: ProviderConfiguration,
        desired: DesiredConfiguration,
        server_name: str,
        reserved_identifier: str | None,
    ) -> Operation:
        if not self._supported:
            raise ProvisionUnsupportedError("provision_server is not supported")
        if self.on_request is not None:
            self.on_request()
        started_at = self._clock()
        identifier = reserved_identifier or str(self._take_identifier())
        operation = Operation(
            operation_id=OperationId(uuid.uuid4().hex),
            configuration_id=configuration.configuration_id,
            server_identifier=identifier,
            action=PowerAction.PROVISION,
            state=OperationState.RUNNING,
            started_at=started_at,
            provider_reference=f"fake-create-{len(self.requests) + 1}",
        )
        if self._rejection is not None:
            return replace(
                operation, server_identifier=None, provider_reference=None
            ).ended(OperationState.FAILED, started_at, str(self._rejection))
        if self._answers_unknown:
            self.requests.append((server_name, reserved_identifier))
            return operation.ended(
                OperationState.UNKNOWN,
                started_at,
                "the request may have reached the provider",
            )
        self.requests.append((server_name, reserved_identifier))
        self._steps[operation.operation_id] = 0
        self._creating[operation.operation_id] = Server(
            identifier, server_name, "running", "fake", (), False
        )
        return operation

    def operation_state(
        self, configuration: ProviderConfiguration, operation: Operation
    ) -> Operation:
        if operation.has_ended:
            return operation
        steps = self._steps.get(operation.operation_id, 0) + 1
        self._steps[operation.operation_id] = steps
        if steps < self._steps_to_finish:
            return operation
        finished_at = max(self._clock(), operation.started_at)
        if self._operations_fail:
            return operation.ended(
                OperationState.FAILED, finished_at, "the fake creation failed"
            )
        created = self._creating.pop(operation.operation_id, None)
        if created is not None:
            self._servers = (*self._servers, created)
        return operation.ended(OperationState.SUCCEEDED, finished_at, None)

    def _take_identifier(self) -> int:
        identifier = self._next_identifier
        self._next_identifier += 1
        return identifier


class FakeRemovalProvider(FakeProvider):
    """Fake ProviderPort and ServerRemovalPort with scripted behaviour.

    ``kind`` DESTROY blocks a running server and takes a destroyed server out
    of ``list_servers``; CANCEL blocks a cancelled one and leaves a removed
    server listed with the state ``cancelled``, as Contabo does. A request
    returns a RUNNING operation; each ``operation_state`` call is one step, and
    the operation ends after ``steps_to_finish`` steps. Every accepted request
    is in ``requests`` as a server identifier; ``supported`` False leaves the
    capability out, so a request is refused as unsupported.
    """

    def __init__(
        self,
        servers: tuple[Server, ...] = (),
        kind: RemovalKind = RemovalKind.DESTROY,
        supported: bool = True,
        steps_to_finish: int = 2,
        effective_date: str | None = "2026-11-18",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        names = (LIST_SERVERS, LIST_SERVER_OPTIONS, INSPECT_SERVER)
        super().__init__(
            capabilities=frozenset(
                Capability(name)
                for name in (*names, *((REMOVE_SERVER,) if supported else ()))
            ),
            servers=servers,
        )
        self._kind = kind
        self._supported = supported
        self._steps_to_finish = steps_to_finish
        self._effective_date = effective_date
        self._clock = clock
        self._rejection: ProviderError | None = None
        self._operations_fail = False
        self._answers_unknown = False
        self._steps: dict[OperationId, int] = {}
        self.requests: list[str] = []
        self.reads: list[str] = []

    def reject_with(self, error: ProviderError | None) -> None:
        """Make removals fail with ``error``; None makes them succeed again."""
        self._rejection = error

    def answer_unknown(self) -> None:
        """Make a removal end at once as UNKNOWN, as after a lost answer."""
        self._answers_unknown = True

    def make_operations_fail(self) -> None:
        self._operations_fail = True

    def removal_kind(self, configuration: ProviderConfiguration) -> RemovalKind:
        self.reads.append("removal_kind")
        return self._kind

    def removal_block(
        self, configuration: ProviderConfiguration, server: Server
    ) -> RemovalBlock | None:
        self.reads.append("removal_block")
        if self._kind is RemovalKind.DESTROY and server.state == "running":
            return RemovalBlock.RUNNING
        if self._kind is RemovalKind.CANCEL and server.state == "cancelled":
            return RemovalBlock.ALREADY_REMOVED
        return None

    def consequence(self, configuration: ProviderConfiguration, server: Server) -> str:
        self.reads.append("consequence")
        if self._kind is RemovalKind.DESTROY:
            return f"This destroys server {server.server_identifier} now."
        return (
            f"This requests the cancellation of server {server.server_identifier}; "
            "the contract runs on until the effective date the provider reports."
        )

    def request_removal(
        self, configuration: ProviderConfiguration, server: Server
    ) -> Operation:
        if not self._supported:
            raise RemovalUnsupportedError("remove_server is not supported")
        started_at = self._clock()
        operation = Operation(
            operation_id=OperationId(uuid.uuid4().hex),
            configuration_id=configuration.configuration_id,
            server_identifier=server.server_identifier,
            action=PowerAction.REMOVE,
            state=OperationState.RUNNING,
            started_at=started_at,
            provider_reference=f"fake-remove-{len(self.requests) + 1}",
        )
        if self._rejection is not None:
            return replace(operation, provider_reference=None).ended(
                OperationState.FAILED, started_at, str(self._rejection)
            )
        self.requests.append(server.server_identifier)
        if self._answers_unknown:
            return operation.ended(
                OperationState.UNKNOWN,
                started_at,
                "the request may have reached the provider",
            )
        self._steps[operation.operation_id] = 0
        return operation

    def operation_state(
        self, configuration: ProviderConfiguration, operation: Operation
    ) -> Operation:
        if operation.has_ended:
            return operation
        steps = self._steps.get(operation.operation_id, 0) + 1
        self._steps[operation.operation_id] = steps
        if steps < self._steps_to_finish:
            return operation
        finished_at = max(self._clock(), operation.started_at)
        if self._operations_fail:
            return operation.ended(
                OperationState.FAILED, finished_at, "the fake removal failed"
            )
        self._apply(operation)
        return operation.ended(
            OperationState.SUCCEEDED, finished_at, self._outcome_detail()
        )

    def _apply(self, operation: Operation) -> None:
        if self._kind is RemovalKind.DESTROY:
            self._servers = tuple(
                s
                for s in self._servers
                if s.server_identifier != operation.server_identifier
            )
            return
        self._servers = tuple(
            replace(s, state="cancelled")
            if s.server_identifier == operation.server_identifier
            else s
            for s in self._servers
        )

    def _outcome_detail(self) -> str | None:
        if self._kind is RemovalKind.DESTROY:
            return None
        if self._effective_date is None:
            return "cancelled, no date was reported"
        return f"cancelled, effective {self._effective_date}"
