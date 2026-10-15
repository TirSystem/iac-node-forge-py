"""A named reference to a secret kept by the platform."""

from dataclasses import dataclass

from iac_node_forge.domain._validation import require_text


@dataclass(frozen=True, slots=True)
class CredentialReference:
    """Names a secret kept by the platform credential store."""

    name: str
    store_kind: str

    def __post_init__(self) -> None:
        require_text("CredentialReference", "name", self.name)
        require_text("CredentialReference", "store_kind", self.store_kind)
