"""Read-only adapter for the Contabo API."""

from iac_node_forge.infrastructure.contabo.provider import (
    DEFAULT_AUTH_URL,
    ContaboProvider,
)

__all__ = ["DEFAULT_AUTH_URL", "ContaboProvider"]
