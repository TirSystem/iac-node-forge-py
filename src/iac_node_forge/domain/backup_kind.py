"""The two ways a provider keeps a backup of a whole server."""

from enum import StrEnum


class BackupKind(StrEnum):
    """What a backup is at the provider (ADR-0010).

    ARCHIVE is a file on a storage that accepts backups (Proxmox VE). SNAPSHOT
    is held by the provider on the same disk system as the server, so it is not
    an independent copy (Contabo).
    """

    ARCHIVE = "archive"
    SNAPSHOT = "snapshot"
