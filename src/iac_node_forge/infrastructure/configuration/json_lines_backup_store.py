"""BackupStore kept in the client's JSON Lines file (ERD-001 BackupRecord).

The records live in the same file as the configuration, as ``backup`` lines;
every other record is written back unchanged. A record says where a backup this
client made is kept and never holds a secret. Nothing here deletes a record.
"""

from pathlib import Path

from iac_node_forge.domain.backup_record import BackupRecord
from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.infrastructure.configuration._backup_rows import key_of
from iac_node_forge.infrastructure.configuration._json_lines_file import (
    read_snapshot,
    write_snapshot,
)


class JsonLinesBackupStore:
    """Keeps backup records as lines of the client's JSON Lines file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, record: BackupRecord) -> None:
        """Insert the record, or replace the one with the same key."""
        snapshot = read_snapshot(self._path)
        snapshot.backups[key_of(record)] = record
        write_snapshot(self._path, snapshot)

    def load(
        self,
        configuration_id: ConfigurationId,
        server_identifier: str,
        backup_id: str,
    ) -> BackupRecord | None:
        """Return the record with this key, or None."""
        backups = read_snapshot(self._path).backups
        return backups.get((configuration_id, server_identifier, backup_id))

    def load_for(
        self, configuration_id: ConfigurationId, server_identifier: str
    ) -> tuple[BackupRecord, ...]:
        """Return the records of one server, oldest first."""
        records = [
            record
            for record in read_snapshot(self._path).backups.values()
            if record.configuration_id == configuration_id
            and record.server_identifier == server_identifier
        ]
        return tuple(sorted(records, key=lambda r: (r.created_at, r.backup_id)))

    def load_by_operation(self, operation_id: OperationId) -> BackupRecord | None:
        """Return the record the operation made, or None."""
        return next(
            (
                record
                for record in read_snapshot(self._path).backups.values()
                if record.operation_id == operation_id
            ),
            None,
        )
