"""A supported action or option of a provider."""

from dataclasses import dataclass

from iac_node_forge.domain._validation import require_text


@dataclass(frozen=True, slots=True)
class Capability:
    """Names one supported action or option."""

    name: str

    def __post_init__(self) -> None:
        require_text("Capability", "name", self.name)
