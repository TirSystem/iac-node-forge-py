"""The ``server_access`` record of the JSON Lines file (ERD-001 ServerAccess).

One line per server: the host (null when the provider's address is used), the
port, the user and the identity hint. There is no field for a password, a
passphrase, a key or a token.
"""

from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.server_access import ServerAccess
from iac_node_forge.infrastructure.configuration._rows import (
    Row,
    integer,
    optional_text,
    text,
)

ACCESS = "server_access"
FIELDS: dict[str, frozenset[str]] = {
    ACCESS: frozenset(
        {
            "record_type",
            "configuration_id",
            "server_identifier",
            "host",
            "port",
            "user",
            "identity_hint",
        }
    )
}
NULLABLE = frozenset({"host", "identity_hint"})
INTEGER = frozenset({"port"})

type AccessKey = tuple[ConfigurationId, str]


def access_row(access: ServerAccess) -> Row:
    """Return the line of a server access."""
    return {
        "record_type": ACCESS,
        "configuration_id": access.configuration_id,
        "server_identifier": access.server_identifier,
        "host": access.host,
        "port": access.port,
        "user": access.user,
        "identity_hint": access.identity_hint,
    }


def access_from_row(row: Row) -> ServerAccess:
    """Return the server access of a line."""
    return ServerAccess(
        configuration_id=ConfigurationId(text(row, "configuration_id")),
        server_identifier=text(row, "server_identifier"),
        host=optional_text(row, "host"),
        port=integer(row, "port"),
        user=text(row, "user"),
        identity_hint=optional_text(row, "identity_hint"),
    )
