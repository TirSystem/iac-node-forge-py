"""The saved settings for one provider connection."""

from dataclasses import dataclass
from typing import NewType

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.provider_kind import ProviderKind

ConfigurationId = NewType("ConfigurationId", str)


@dataclass(frozen=True, slots=True)
class ProviderConfiguration:
    """Holds the saved settings for one provider connection, without a secret."""

    configuration_id: ConfigurationId
    provider_kind: ProviderKind
    display_name: str
    address: str
    credential_reference: CredentialReference | None = None

    def __post_init__(self) -> None:
        require_text("ProviderConfiguration", "configuration_id", self.configuration_id)
        require_text("ProviderConfiguration", "display_name", self.display_name)
        require_text("ProviderConfiguration", "address", self.address)
