"""The boundary to a provider's interface."""

from typing import Protocol, runtime_checkable

from iac_node_forge.domain.access_result import AccessResult
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_option import ServerOption


@runtime_checkable
class ProviderPort(Protocol):
    """Read-only operations of one provider; one adapter per provider."""

    def validate_access(self, address: str, credentials: Credentials) -> AccessResult:
        """Check that the address accepts the credentials."""
        ...

    def discover_capabilities(
        self, configuration: ProviderConfiguration
    ) -> frozenset[Capability]:
        """Return the capabilities the provider declares."""
        ...

    def list_server_options(
        self, configuration: ProviderConfiguration, option_kind: str
    ) -> tuple[ServerOption, ...]:
        """Return the options of one kind the provider offers.

        Raises UnknownOptionKindError for a kind the provider does not offer.
        """
        ...

    def list_servers(self, configuration: ProviderConfiguration) -> tuple[Server, ...]:
        """Return the servers at the provider."""
        ...

    def get_server(
        self, configuration: ProviderConfiguration, server_identifier: str
    ) -> Server:
        """Return one server by its provider identifier.

        Raises ServerNotFoundError when the provider has no such server.
        """
        ...
