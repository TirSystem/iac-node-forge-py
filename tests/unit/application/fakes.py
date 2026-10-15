"""In-memory fakes of the application ports, for tests."""

import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from iac_node_forge.application.errors import (
    CredentialEntryInvalidError,
    CredentialNotFoundError,
    CredentialStoreUnavailableError,
    OperationNotFoundError,
    PowerActionUnsupportedError,
    ProviderError,
    ServerNotFoundError,
    UnknownOptionKindError,
    UnsupportedProviderError,
)
from iac_node_forge.application.ports import ProviderPort, ServerPowerPort
from iac_node_forge.domain.access_result import AccessResult
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.operation import (
    Operation,
    OperationId,
    OperationState,
)
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_option import ServerOption

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
    ) -> None:
        self._default = default
        self._by_configuration = by_configuration or {}
        self._power = power
        self._power_by_configuration = power_by_configuration or {}
        self.resolved: list[ConfigurationId] = []
        self.power_resolved: list[ConfigurationId] = []

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
        supported: frozenset[PowerAction] = frozenset(PowerAction),
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
