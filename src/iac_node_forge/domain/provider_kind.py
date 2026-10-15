"""The providers a configuration can connect to."""

from enum import Enum


class ProviderKind(Enum):
    """Names the provider a configuration connects to."""

    PROXMOX_VE = "proxmox_ve"
    CONTABO = "contabo"
