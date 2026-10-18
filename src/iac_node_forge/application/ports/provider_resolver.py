"""The boundary to getting a provider port for a saved configuration."""

from typing import Protocol, runtime_checkable

from iac_node_forge.application.ports.provider_port import ProviderPort
from iac_node_forge.application.ports.server_power_port import ServerPowerPort
from iac_node_forge.application.ports.server_provision_port import (
    ServerProvisionPort,
)
from iac_node_forge.application.ports.server_removal_port import ServerRemovalPort
from iac_node_forge.domain.provider_configuration import ProviderConfiguration


@runtime_checkable
class ProviderResolver(Protocol):
    """Builds the ports for a configuration, with its credentials.

    A provider adapter takes its credentials at construction, and which
    credentials and which adapter depend on the configuration, so a use case
    that serves several configurations asks for the port per configuration.
    """

    def provider_for(self, configuration: ProviderConfiguration) -> ProviderPort:
        """Return the port for a configuration.

        Raises UnsupportedProviderError when no adapter exists for its kind and
        a CredentialStoreError when its credentials cannot be obtained.
        """
        ...

    def power_for(self, configuration: ProviderConfiguration) -> ServerPowerPort:
        """Return the power port for a configuration.

        Raises UnsupportedProviderError when its adapter has no power actions
        and a CredentialStoreError when its credentials cannot be obtained.
        """
        ...

    def provision_for(
        self, configuration: ProviderConfiguration
    ) -> ServerProvisionPort:
        """Return the provision port for a configuration.

        Raises UnsupportedProviderError when its adapter cannot create servers
        and a CredentialStoreError when its credentials cannot be obtained.
        """
        ...

    def removal_for(self, configuration: ProviderConfiguration) -> ServerRemovalPort:
        """Return the removal port for a configuration.

        Raises UnsupportedProviderError when its adapter cannot remove servers
        and a CredentialStoreError when its credentials cannot be obtained.
        """
        ...
