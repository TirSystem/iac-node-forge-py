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
