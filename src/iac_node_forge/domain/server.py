"""A server at a provider."""

from dataclasses import dataclass

from iac_node_forge.domain._validation import require_text


@dataclass(frozen=True, slots=True)
class Server:
    """Describes a server and whether this client manages it."""

    server_identifier: str
    name: str
    state: str
    size: str
    addresses: tuple[str, ...]
    managed: bool

    def __post_init__(self) -> None:
        require_text("Server", "server_identifier", self.server_identifier)
        require_text("Server", "name", self.name)
        require_text("Server", "state", self.state)
