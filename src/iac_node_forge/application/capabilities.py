"""The actions the product knows, by the name a provider declares them under.

The set a provider supports is declared by its adapter (ADR-0002); this list
lets a client show the others as unsupported instead of hiding them.
"""

from collections.abc import Mapping
from types import MappingProxyType

from iac_node_forge.domain.power_action import PowerAction

LIST_SERVERS = "list_servers"
LIST_SERVER_OPTIONS = "list_server_options"
INSPECT_SERVER = "inspect_server"
PROVISION_SERVER = "provision_server"
START_SERVER = "start_server"
SHUTDOWN_SERVER = "shutdown_server"
STOP_SERVER = "stop_server"
RESTART_SERVER = "restart_server"
REMOVE_SERVER = "remove_server"
BACKUP_SERVER = "backup_server"
RESTORE_SERVER = "restore_server"

KNOWN_ACTIONS: tuple[str, ...] = (
    LIST_SERVERS,
    LIST_SERVER_OPTIONS,
    INSPECT_SERVER,
    PROVISION_SERVER,
    START_SERVER,
    SHUTDOWN_SERVER,
    STOP_SERVER,
    RESTART_SERVER,
    REMOVE_SERVER,
    BACKUP_SERVER,
    RESTORE_SERVER,
)

POWER_ACTION_CAPABILITY: Mapping[PowerAction, str] = MappingProxyType(
    {
        PowerAction.START: START_SERVER,
        PowerAction.SHUTDOWN: SHUTDOWN_SERVER,
        PowerAction.STOP: STOP_SERVER,
        PowerAction.RESTART: RESTART_SERVER,
    }
)
"""The capability name a provider declares for each power action, defined once."""

ACTION_CAPABILITY: Mapping[PowerAction, str] = MappingProxyType(
    {
        **POWER_ACTION_CAPABILITY,
        PowerAction.PROVISION: PROVISION_SERVER,
        PowerAction.REMOVE: REMOVE_SERVER,
        PowerAction.BACKUP: BACKUP_SERVER,
        PowerAction.RESTORE: RESTORE_SERVER,
    }
)
"""The capability name of every action of an operation, PROVISION, REMOVE,
BACKUP and RESTORE included (listing backups belongs to ``backup_server``)."""

POWER_CAPABILITIES: frozenset[str] = frozenset(POWER_ACTION_CAPABILITY.values())
