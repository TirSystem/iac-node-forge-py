"""The record that this client manages a server."""

from dataclasses import dataclass, replace
from datetime import datetime

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.errors import ManagedServerEndError, NaiveTimestampError
from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.provider_configuration import ConfigurationId


@dataclass(frozen=True, slots=True)
class ManagedServerRecord:
    """Records that this client manages a server under a configuration.

    A removal ends the record, it never deletes it (ADR-0008): ``ended_at`` and
    ``ended_by_operation_id`` are both set or both None, and an ended record no
    longer counts as managed.
    """

    configuration_id: ConfigurationId
    server_identifier: str
    recorded_at: datetime
    ended_at: datetime | None = None
    ended_by_operation_id: OperationId | None = None

    def __post_init__(self) -> None:
        require_text("ManagedServerRecord", "configuration_id", self.configuration_id)
        require_text("ManagedServerRecord", "server_identifier", self.server_identifier)
        if self.recorded_at.tzinfo is None:
            raise NaiveTimestampError(
                "ManagedServerRecord.recorded_at needs a timezone"
            )
        self._check_end()

    @property
    def is_ended(self) -> bool:
        """Whether a removal has ended this record."""
        return self.ended_at is not None

    def ended(
        self, ended_at: datetime, operation_id: OperationId
    ) -> "ManagedServerRecord":
        """Return this record, ended at ``ended_at`` by the operation."""
        return replace(self, ended_at=ended_at, ended_by_operation_id=operation_id)

    def _check_end(self) -> None:
        if (self.ended_at is None) != (self.ended_by_operation_id is None):
            raise ManagedServerEndError(
                "ManagedServerRecord needs ended_at and ended_by_operation_id "
                "together or neither"
            )
        if self.ended_at is not None and self.ended_at.tzinfo is None:
            raise NaiveTimestampError("ManagedServerRecord.ended_at needs a timezone")
        if self.ended_by_operation_id is not None:
            require_text(
                "ManagedServerRecord",
                "ended_by_operation_id",
                self.ended_by_operation_id,
            )
