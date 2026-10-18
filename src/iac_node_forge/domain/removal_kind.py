"""The two ways a provider ends a server."""

from enum import StrEnum


class RemovalKind(StrEnum):
    """What a removal does at the provider (ADR-0008).

    DESTROY ends the server at once and releases its disks (Proxmox VE). CANCEL
    only requests a cancellation: the contract and its billing run on until an
    effective date the provider reports (Contabo).
    """

    DESTROY = "destroy"
    CANCEL = "cancel"
