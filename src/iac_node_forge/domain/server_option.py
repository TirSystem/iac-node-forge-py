"""A choice offered by a provider."""

from dataclasses import dataclass

from iac_node_forge.domain._validation import require_text


@dataclass(frozen=True, slots=True)
class ServerOption:
    """One choice offered by a provider, such as a size or an image."""

    kind: str
    value: str

    def __post_init__(self) -> None:
        require_text("ServerOption", "kind", self.kind)
        require_text("ServerOption", "value", self.value)
