"""One named provider-specific value of a desired configuration."""

from dataclasses import dataclass

from iac_node_forge.domain._validation import require_text


@dataclass(frozen=True, slots=True)
class ProviderValue:
    """A node, a storage, a region, a product, a period or a size, by name."""

    name: str
    value: str

    def __post_init__(self) -> None:
        require_text("ProviderValue", "name", self.name)
        require_text("ProviderValue", "value", self.value)
