"""The actions the product knows, by the name a provider declares them under.

The set a provider supports is declared by its adapter (ADR-0002); this list
lets a client show the others as unsupported instead of hiding them.
"""

LIST_SERVERS = "list_servers"
LIST_SERVER_OPTIONS = "list_server_options"
INSPECT_SERVER = "inspect_server"
PROVISION_SERVER = "provision_server"
START_SERVER = "start_server"
STOP_SERVER = "stop_server"
RESTART_SERVER = "restart_server"
DELETE_SERVER = "delete_server"

KNOWN_ACTIONS: tuple[str, ...] = (
    LIST_SERVERS,
    LIST_SERVER_OPTIONS,
    INSPECT_SERVER,
    PROVISION_SERVER,
    START_SERVER,
    STOP_SERVER,
    RESTART_SERVER,
    DELETE_SERVER,
)
