"""Gets the credentials of a configuration and the provider port that uses them."""

from typing import TextIO

from iac_node_forge.application.errors import (
    CredentialEntryInvalidError,
    CredentialNotFoundError,
    CredentialStoreUnavailableError,
    UnsupportedProviderError,
)
from iac_node_forge.application.ports import (
    CredentialPrompt,
    CredentialStore,
    ProviderPort,
    ServerPowerPort,
    ServerProvisionPort,
)
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.services import CliServices, ProviderFactory


class CredentialSource:
    """Gives the credentials of a configuration, as ADR-0003 says.

    A configuration with a credential reference is tried against the
    credential store first; when the store is unavailable or holds no secret,
    or the configuration has no reference, the user is asked. An entry that is
    unreadable, or does not hold the values the provider needs, is ignored with
    one line on ``stderr`` that names the reference and never its content.
    """

    def __init__(
        self, store: CredentialStore, prompt: CredentialPrompt, stderr: TextIO
    ) -> None:
        self._store = store
        self._prompt = prompt
        self._stderr = stderr

    def credentials_for(self, configuration: ProviderConfiguration) -> Credentials:
        """Return stored credentials, or ask the user for them."""
        reference = configuration.credential_reference
        if reference is not None:
            stored = self._stored(reference, configuration.provider_kind)
            if stored is not None:
                return stored
        return self._prompt.ask(configuration.provider_kind)

    def _stored(
        self, reference: CredentialReference, kind: ProviderKind
    ) -> Credentials | None:
        try:
            credentials = self._store.get(reference)
        except (CredentialStoreUnavailableError, CredentialNotFoundError):
            return None
        except CredentialEntryInvalidError:
            self._warn_ignored(reference.name)
            return None
        if set(credentials.names) != set(kind.credential_names):
            self._warn_ignored(reference.name)
            return None
        return credentials

    def _warn_ignored(self, reference_name: str) -> None:
        write_line(
            self._stderr,
            f"warning: the stored credential entry {reference_name!r} is not "
            "usable and was ignored; the credentials are asked for instead",
        )


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

    def power_for(self, configuration: ProviderConfiguration) -> ServerPowerPort:
        """Return the power port of the configuration's adapter.

        One adapter object serves both ports, so the credentials are asked for
        once per run whichever port is asked first.
        """
        provider = self.provider_for(configuration)
        if not isinstance(provider, ServerPowerPort):
            raise UnsupportedProviderError(
                f"no power actions for {configuration.provider_kind.value} "
                "in this version of the client"
            )
        return provider

    def provision_for(
        self, configuration: ProviderConfiguration
    ) -> ServerProvisionPort:
        """Return the provision port of the configuration's adapter.

        One adapter object serves all three ports, as for ``power_for``.
        """
        provider = self.provider_for(configuration)
        if not isinstance(provider, ServerProvisionPort):
            raise UnsupportedProviderError(
                f"no provisioning for {configuration.provider_kind.value} "
                "in this version of the client"
            )
        return provider


def build_resolver(services: CliServices) -> CliProviderResolver:
    """Wire the resolver of a command from its services."""
    source = CredentialSource(
        services.credential_store, services.credential_prompt, services.stderr
    )
    return CliProviderResolver(source, services.provider_factory)
