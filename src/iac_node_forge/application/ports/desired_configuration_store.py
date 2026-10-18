"""The boundary to the saved desired configurations."""

from typing import Protocol, runtime_checkable

from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.provider_configuration import ConfigurationId


@runtime_checkable
class DesiredConfigurationStore(Protocol):
    """Keeps desired configurations; one never holds a secret (ADR-0007)."""

    def save(self, desired: DesiredConfiguration) -> None:
        """Persist a desired configuration, replacing the one with the same key.

        The key is the configuration identifier and the desired name.
        """
        ...

    def load(
        self, configuration_id: ConfigurationId, desired_name: str
    ) -> DesiredConfiguration | None:
        """Return the desired configuration with this key, or None when absent."""
        ...

    def load_all(
        self, configuration_id: ConfigurationId
    ) -> tuple[DesiredConfiguration, ...]:
        """Return the desired configurations of one provider configuration."""
        ...
