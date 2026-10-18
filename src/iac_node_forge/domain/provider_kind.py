"""The providers a configuration can connect to."""

from enum import Enum


class ProviderKind(Enum):
    """Names the provider a configuration connects to."""

    PROXMOX_VE = "proxmox_ve"
    CONTABO = "contabo"

    @property
    def credential_names(self) -> tuple[str, ...]:
        """Return the names of the secret values this provider needs, in order."""
        if self is ProviderKind.CONTABO:
            return ("client_id", "client_secret", "api_user", "api_password")
        return ("token",)

    @property
    def option_kinds(self) -> tuple[str, ...]:
        """Return the kinds of server option this provider offers."""
        if self is ProviderKind.CONTABO:
            return ("region", "image", "product")
        return ("node", "storage", "template")

    @property
    def default_address(self) -> str | None:
        """Return the address of the provider's public API, or None when each
        installation has its own."""
        if self is ProviderKind.CONTABO:
            return "https://api.contabo.com"
        return None
