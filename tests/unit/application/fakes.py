"""In-memory fakes of the application ports, for tests."""

from iac_node_forge.application.errors import (
    CredentialEntryInvalidError,
    CredentialNotFoundError,
    CredentialStoreUnavailableError,
    ProviderError,
    ServerNotFoundError,
    UnknownOptionKindError,
)
from iac_node_forge.application.ports import ProviderPort
from iac_node_forge.domain.access_result import AccessResult
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
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
    """Fake ProviderResolver: one provider per configuration id, or one for all."""

    def __init__(
        self,
        default: ProviderPort,
        by_configuration: dict[ConfigurationId, ProviderPort] | None = None,
    ) -> None:
        self._default = default
        self._by_configuration = by_configuration or {}
        self.resolved: list[ConfigurationId] = []

    def provider_for(self, configuration: ProviderConfiguration) -> ProviderPort:
        self.resolved.append(configuration.configuration_id)
        return self._by_configuration.get(configuration.configuration_id, self._default)
