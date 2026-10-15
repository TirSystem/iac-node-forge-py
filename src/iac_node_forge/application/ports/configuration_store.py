"""The boundary to the client's saved configuration."""

from typing import Protocol, runtime_checkable

from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)


@runtime_checkable
class ConfigurationStore(Protocol):
    """Keeps provider configurations and the servers this client manages."""

    def save(self, configuration: ProviderConfiguration) -> None:
        """Persist a configuration."""
        ...

    def load_all(self) -> tuple[ProviderConfiguration, ...]:
        """Return every saved configuration."""
        ...

    def managed_servers(
        self, configuration_id: ConfigurationId
    ) -> tuple[ManagedServerRecord, ...]:
        """Return the servers managed under one configuration."""
        ...
