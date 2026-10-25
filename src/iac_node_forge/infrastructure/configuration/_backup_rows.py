"""The ``backup`` record of the JSON Lines file (ERD-001 BackupRecord).

One line per backup this client made: the key (configuration, server, backup),
the kind, the place it is kept, the creation time the provider reported and the
operation that made it. There is no field for a credential, a token or a key.
"""

from datetime import datetime

from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.backup_record import BackupRecord
from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.infrastructure.configuration._rows import Row, text

BACKUP = "backup"
FIELDS: dict[str, frozenset[str]] = {
    BACKUP: frozenset(
        {
            "record_type",
            "configuration_id",
            "server_identifier",
            "backup_id",
            "kind",
            "location",
            "created_at",
            "operation_id",
        }
    )
}

type BackupKey = tuple[ConfigurationId, str, str]


def key_of(record: BackupRecord) -> BackupKey:
    """Return the key the file keeps a record under."""
    return record.configuration_id, record.server_identifier, record.backup_id


def backup_row(record: BackupRecord) -> Row:
    """Return the line of a backup record."""
    return {
        "record_type": BACKUP,
        "configuration_id": record.configuration_id,
        "server_identifier": record.server_identifier,
        "backup_id": record.backup_id,
        "kind": record.kind.value,
        "location": record.location,
        "created_at": record.created_at.isoformat(),
        "operation_id": record.operation_id,
    }


def backup_from_row(row: Row) -> BackupRecord:
    """Return the backup record of a line."""
    return BackupRecord(
        configuration_id=ConfigurationId(text(row, "configuration_id")),
        server_identifier=text(row, "server_identifier"),
        backup_id=text(row, "backup_id"),
        kind=BackupKind(text(row, "kind")),
        location=text(row, "location"),
        created_at=datetime.fromisoformat(text(row, "created_at")),
        operation_id=OperationId(text(row, "operation_id")),
    )
