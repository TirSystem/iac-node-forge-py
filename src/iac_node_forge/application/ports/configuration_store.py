"""The boundary to the client's saved configuration."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.operation import OperationId
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
        """Return the servers managed under one configuration, not the ended."""
        ...

    def save_managed_server(self, record: ManagedServerRecord) -> None:
        """Record that a server is managed; recording it again changes nothing."""
        ...

    def end_managed_server(
        self,
        configuration_id: ConfigurationId,
        server_identifier: str,
        ended_at: datetime,
        operation_id: OperationId,
    ) -> None:
        """End the record of a managed server, keeping it; ending twice changes nothing.

        Nothing happens, and nothing is raised, when no active record exists.
        """
        ...
