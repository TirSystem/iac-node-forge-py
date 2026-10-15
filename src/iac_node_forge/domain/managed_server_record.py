"""The record that this client manages a server."""

from dataclasses import dataclass
from datetime import datetime

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.errors import NaiveTimestampError
from iac_node_forge.domain.provider_configuration import ConfigurationId


@dataclass(frozen=True, slots=True)
class ManagedServerRecord:
    """Records that this client manages a server under a configuration."""

    configuration_id: ConfigurationId
    server_identifier: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        require_text("ManagedServerRecord", "configuration_id", self.configuration_id)
        require_text("ManagedServerRecord", "server_identifier", self.server_identifier)
        if self.recorded_at.tzinfo is None:
            raise NaiveTimestampError(
                "ManagedServerRecord.recorded_at needs a timezone"
            )
