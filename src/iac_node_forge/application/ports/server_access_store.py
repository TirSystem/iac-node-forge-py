"""The boundary to the connection facts kept per server."""

from typing import Protocol, runtime_checkable

from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.server_access import ServerAccess


@runtime_checkable
class ServerAccessStore(Protocol):
    """Keeps the host, port, user and identity hint of a server (ADR-0009)."""

    def save(self, access: ServerAccess) -> None:
        """Persist the access, replacing the one of the same server."""
        ...

    def load(
        self, configuration_id: ConfigurationId, server_identifier: str
    ) -> ServerAccess | None:
        """Return the access recorded for the server, or None."""
        ...
