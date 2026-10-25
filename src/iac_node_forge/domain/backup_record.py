"""The record of a backup that this client made."""

from dataclasses import dataclass
from datetime import datetime

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.errors import NaiveTimestampError
from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.provider_configuration import ConfigurationId


@dataclass(frozen=True, slots=True)
class BackupRecord:
    """Says where a backup made by this client is kept (ADR-0010).

    Unique by (configuration_id, server_identifier, backup_id) and linked to the
    operation that made it. It is never deleted by this client and has no field
    for a credential, a token or a key.
    """

    configuration_id: ConfigurationId
    server_identifier: str
    backup_id: str
    kind: BackupKind
    location: str
    created_at: datetime
    operation_id: OperationId

    def __post_init__(self) -> None:
        require_text("BackupRecord", "configuration_id", self.configuration_id)
        require_text("BackupRecord", "server_identifier", self.server_identifier)
        require_text("BackupRecord", "backup_id", self.backup_id)
        require_text("BackupRecord", "location", self.location)
        require_text("BackupRecord", "operation_id", self.operation_id)
        if self.created_at.tzinfo is None:
            raise NaiveTimestampError("BackupRecord.created_at needs a timezone")
