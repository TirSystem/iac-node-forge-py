"""Adapter for the Contabo API: reads and the four power actions."""

from iac_node_forge.infrastructure.contabo.provider import (
    DEFAULT_AUTH_URL,
    DEFAULT_RESTART_SETTLE_SECONDS,
    ContaboProvider,
)

__all__ = ["DEFAULT_AUTH_URL", "DEFAULT_RESTART_SETTLE_SECONDS", "ContaboProvider"]
