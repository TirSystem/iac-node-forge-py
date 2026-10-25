"""The boundary to the records of backups this client made."""

from typing import Protocol, runtime_checkable

from iac_node_forge.domain.backup_record import BackupRecord
from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.provider_configuration import ConfigurationId


@runtime_checkable
class BackupStore(Protocol):
    """Keeps backup records; a record never holds a secret (ADR-0010)."""

    def save(self, record: BackupRecord) -> None:
        """Persist a record, replacing the one with the same key."""
        ...

    def load(
        self,
        configuration_id: ConfigurationId,
        server_identifier: str,
        backup_id: str,
    ) -> BackupRecord | None:
        """Return the record with this key, or None."""
        ...

    def load_for(
        self, configuration_id: ConfigurationId, server_identifier: str
    ) -> tuple[BackupRecord, ...]:
        """Return the records of one server."""
        ...

    def load_by_operation(self, operation_id: OperationId) -> BackupRecord | None:
        """Return the record the operation made, or None."""
        ...
