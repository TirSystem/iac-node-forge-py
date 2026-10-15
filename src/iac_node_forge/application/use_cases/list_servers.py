"""UC-007: list and inspect servers across the configured providers."""

from iac_node_forge.application.dto import (
    FailureCategory,
    ProviderFailure,
    ServerListing,
    ServerSummary,
)
from iac_node_forge.application.errors import (
    CredentialStoreError,
    NoConfigurationError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderUnreachableError,
)
from iac_node_forge.application.ports import ConfigurationStore, ProviderResolver
from iac_node_forge.application.use_cases._configuration_lookup import (
    find_configuration,
)
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.server import Server


class ListServers:
    """Reads servers and marks those this client manages; changes nothing."""

    def __init__(
        self, providers: ProviderResolver, configurations: ConfigurationStore
    ) -> None:
        self._providers = providers
        self._configurations = configurations

    def list_servers(self, configuration: str | None = None) -> ServerListing:
        """List the servers of every configuration, or of the one named.

        A provider that cannot be read is reported in ``failures`` and does not
        hide the servers of the others. Raises NoConfigurationError when
        nothing is configured.
        """
        servers: list[ServerSummary] = []
        failures: list[ProviderFailure] = []
        for target in self._targets(configuration):
            try:
                provider = self._providers.provider_for(target)
                provided = provider.list_servers(target)
            except (ProviderError, CredentialStoreError) as error:
                failures.append(_failure(target, error))
                continue
            servers.extend(self._summaries(target, provided))
        return ServerListing(tuple(servers), tuple(failures))

    def inspect_server(
        self, configuration: str, server_identifier: str
    ) -> ServerSummary:
        """Return one server; raises ServerNotFoundError when it does not exist."""
        target = find_configuration(self._configurations, configuration)
        provider = self._providers.provider_for(target)
        server = provider.get_server(target, server_identifier)
        return self._summaries(target, (server,))[0]

    def _targets(self, configuration: str | None) -> tuple[ProviderConfiguration, ...]:
        if configuration is not None:
            return (find_configuration(self._configurations, configuration),)
        targets = self._configurations.load_all()
        if not targets:
            raise NoConfigurationError("no provider is configured")
        return targets

    def _summaries(
        self, target: ProviderConfiguration, servers: tuple[Server, ...]
    ) -> tuple[ServerSummary, ...]:
        records = self._configurations.managed_servers(target.configuration_id)
        managed = {record.server_identifier for record in records}
        return tuple(
            ServerSummary(
                target.configuration_id,
                target.display_name,
                server.server_identifier,
                server.name,
                server.state,
                server.size,
                server.addresses,
                server.managed or server.server_identifier in managed,
            )
            for server in servers
        )


def _failure(
    target: ProviderConfiguration, error: ProviderError | CredentialStoreError
) -> ProviderFailure:
    if isinstance(error, ProviderUnreachableError):
        category = FailureCategory.UNREACHABLE
    elif isinstance(error, ProviderAuthenticationError):
        category = FailureCategory.REJECTED
    elif isinstance(error, CredentialStoreError):
        category = FailureCategory.CREDENTIALS
    else:
        category = FailureCategory.OTHER
    return ProviderFailure(
        target.configuration_id, target.display_name, category, str(error)
    )
