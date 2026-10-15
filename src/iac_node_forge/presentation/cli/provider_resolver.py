"""Gets the credentials of a configuration and the provider port that uses them."""

from iac_node_forge.application.errors import (
    CredentialNotFoundError,
    CredentialStoreUnavailableError,
)
from iac_node_forge.application.ports import (
    CredentialPrompt,
    CredentialStore,
    ProviderPort,
)
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.presentation.cli.services import CliServices, ProviderFactory


class CredentialSource:
    """Gives the credentials of a configuration, as ADR-0003 says.

    A configuration with a credential reference is tried against the
    credential store first; when the store is unavailable or holds no secret,
    or the configuration has no reference, the user is asked.
    """

    def __init__(self, store: CredentialStore, prompt: CredentialPrompt) -> None:
        self._store = store
        self._prompt = prompt

    def credentials_for(self, configuration: ProviderConfiguration) -> Credentials:
        """Return stored credentials, or ask the user for them."""
        reference = configuration.credential_reference
        if reference is not None:
            try:
                return self._store.get(reference)
            except (CredentialStoreUnavailableError, CredentialNotFoundError):
                pass
        return self._prompt.ask(configuration.provider_kind)


class CliProviderResolver:
    """ProviderResolver that builds each configuration's port once per run."""

    def __init__(self, source: CredentialSource, factory: ProviderFactory) -> None:
        self._source = source
        self._factory = factory
        self._providers: dict[str, ProviderPort] = {}

    def provider_for(self, configuration: ProviderConfiguration) -> ProviderPort:
        """Return the port for a configuration, asking for a secret if needed."""
        key = configuration.configuration_id
        if key not in self._providers:
            credentials = self._source.credentials_for(configuration)
            self._providers[key] = self._factory(
                configuration.provider_kind, credentials
            )
        return self._providers[key]


def build_resolver(services: CliServices) -> CliProviderResolver:
    """Wire the resolver of a command from its services."""
    source = CredentialSource(services.credential_store, services.credential_prompt)
    return CliProviderResolver(source, services.provider_factory)
