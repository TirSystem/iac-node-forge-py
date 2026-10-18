"""ServerAccessStore kept in the client's JSON Lines file (ERD-001 ServerAccess).

The access lives in the same file as the configuration, as a ``server_access``
line per server; every other record is written back unchanged. It holds a host,
a port, a user and a hint, and never a password, a passphrase, a key or a token.
"""

from pathlib import Path

from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.server_access import ServerAccess
from iac_node_forge.infrastructure.configuration._json_lines_file import (
    read_snapshot,
    write_snapshot,
)


class JsonLinesServerAccessStore:
    """Keeps the connection facts of servers as records of the JSON Lines file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, access: ServerAccess) -> None:
        """Insert the access, or replace the one of the same server."""
        snapshot = read_snapshot(self._path)
        snapshot.accesses[(access.configuration_id, access.server_identifier)] = access
        write_snapshot(self._path, snapshot)

    def load(
        self, configuration_id: ConfigurationId, server_identifier: str
    ) -> ServerAccess | None:
        """Return the access recorded for the server, or None."""
        accesses = read_snapshot(self._path).accesses
        return accesses.get((configuration_id, server_identifier))
