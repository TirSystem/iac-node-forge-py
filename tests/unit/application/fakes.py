"""In-memory fakes of the application ports, for tests."""

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
        self._servers = servers

    def validate_access(self, address: str, credentials: Credentials) -> AccessResult:
        if credentials.secret == self._accepted_secret:
            return AccessResult(succeeded=True)
        return AccessResult(succeeded=False, reason="credentials rejected")

    def discover_capabilities(
        self, configuration: ProviderConfiguration
    ) -> frozenset[Capability]:
        return self._capabilities

    def list_server_options(
        self, configuration: ProviderConfiguration, option_kind: str
    ) -> tuple[ServerOption, ...]:
        return tuple(o for o in self._options if o.kind == option_kind)

    def list_servers(self, configuration: ProviderConfiguration) -> tuple[Server, ...]:
        return self._servers

    def get_server(
        self, configuration: ProviderConfiguration, server_identifier: str
    ) -> Server:
        for server in self._servers:
            if server.server_identifier == server_identifier:
                return server
        raise KeyError(server_identifier)


class FakeConfigurationStore:
    """Fake ConfigurationStore keeping data in memory."""

    def __init__(self, managed: tuple[ManagedServerRecord, ...] = ()) -> None:
        self._configurations: dict[ConfigurationId, ProviderConfiguration] = {}
        self._managed = managed

    def save(self, configuration: ProviderConfiguration) -> None:
        self._configurations[configuration.configuration_id] = configuration

    def load_all(self) -> tuple[ProviderConfiguration, ...]:
        return tuple(self._configurations.values())

    def managed_servers(
        self, configuration_id: ConfigurationId
    ) -> tuple[ManagedServerRecord, ...]:
        return tuple(r for r in self._managed if r.configuration_id == configuration_id)


class FakeCredentialStore:
    """Fake CredentialStore keeping secrets in memory."""

    def __init__(self) -> None:
        self._secrets: dict[CredentialReference, Credentials] = {}

    def put(self, reference: CredentialReference, credentials: Credentials) -> None:
        self._secrets[reference] = credentials

    def get(self, reference: CredentialReference) -> Credentials:
        return self._secrets[reference]


class FakeCredentialPrompt:
    """Fake CredentialPrompt answering with a preset secret."""

    def __init__(self, secret: str = "secret") -> None:
        self._secret = secret
        self.asked: list[ProviderKind] = []

    def ask(self, provider_kind: ProviderKind) -> Credentials:
        self.asked.append(provider_kind)
        return Credentials(self._secret)
