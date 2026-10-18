"""The record saved before a create request is sent."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.desired_configuration import require_unique_names
from iac_node_forge.domain.errors import NaiveTimestampError
from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.provider_value import ProviderValue

IntentId = NewType("IntentId", str)


class IntentState(StrEnum):
    """An intent is pending until its operation ends."""

    PENDING = "pending"
    ENDED = "ended"


@dataclass(frozen=True, slots=True)
class ProvisionIntent:
    """What is about to be created, kept so a lost answer is not repeated.

    It copies the chosen values and, for Proxmox VE, the reserved identifier;
    it is linked to its operation by ``operation_id`` once the request is made.
    It never holds a secret.
    """

    intent_id: IntentId
    configuration_id: ConfigurationId
    desired_name: str
    server_name: str
    image: str
    values: tuple[ProviderValue, ...]
    state: IntentState
    recorded_at: datetime
    reserved_identifier: str | None = None
    operation_id: OperationId | None = None

    def __post_init__(self) -> None:
        require_text("ProvisionIntent", "intent_id", self.intent_id)
        require_text("ProvisionIntent", "configuration_id", self.configuration_id)
        require_text("ProvisionIntent", "desired_name", self.desired_name)
        require_text("ProvisionIntent", "server_name", self.server_name)
        require_text("ProvisionIntent", "image", self.image)
        require_unique_names("ProvisionIntent", self.values)
        if self.reserved_identifier is not None:
            require_text(
                "ProvisionIntent", "reserved_identifier", self.reserved_identifier
            )
        if self.operation_id is not None:
            require_text("ProvisionIntent", "operation_id", self.operation_id)
        if self.recorded_at.tzinfo is None:
            raise NaiveTimestampError("ProvisionIntent.recorded_at needs a timezone")
