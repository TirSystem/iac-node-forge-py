"""The boundary to a provider's backup and restore of a whole server."""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.operation import Operation
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.restore_effect import RestoreEffect
from iac_node_forge.domain.server import Server


class BackupBlock(StrEnum):
    """Why a backup or a restore cannot be made now, as the port reports it."""

    NO_BACKUP_STORAGE = "no storage accepts backups"
    STORAGE_UNSUITABLE = "the storage cannot hold a backup"
    RUNNING = "running"
    CANCELLED = "cancelled"
    MISMATCH = "the backup is not of this server"


@runtime_checkable
class ServerBackupPort(Protocol):
    """The backup and restore requests and what is needed around them (ADR-0010).

    A narrow port beside ProviderPort, ServerPowerPort, ServerProvisionPort and
    ServerRemovalPort. Only ``request_backup`` and ``request_restore`` write;
    every other method is a read, so a caller can state the kind, refuse a
    blocked server, show the consequence and list the backups before anything is
    sent.
    """

    def backup_kind(self, configuration: ProviderConfiguration) -> BackupKind:
        """Return what a backup is at this provider: an archive or a snapshot."""
        ...

    def backup_storages(
        self, configuration: ProviderConfiguration, server: Server
    ) -> tuple[str, ...]:
        """Return the storages of ``server`` that accept backups.

        Empty when the provider has no storage to choose (a snapshot) or none
        accepts backups.
        """
        ...

    def backup_block(
        self,
        configuration: ProviderConfiguration,
        server: Server,
        storage: str | None,
    ) -> BackupBlock | None:
        """Return why ``server`` cannot be backed up now, or None when it can.

        ``storage`` is the one the user named, or None when none was named
        (NO_BACKUP_STORAGE then means none exists). Sends no write.
        """
        ...

    def restore_block(
        self,
        configuration: ProviderConfiguration,
        server: Server,
        backup: Backup,
    ) -> BackupBlock | None:
        """Return why ``backup`` cannot be restored over ``server`` now, or None.

        RUNNING, CANCELLED or MISMATCH. Sends no write.
        """
        ...

    def backup_consequence(
        self,
        configuration: ProviderConfiguration,
        server: Server,
        storage: str | None,
    ) -> str:
        """Return the text that states what a backup of ``server`` does.

        Shown before the confirmation and recorded with the operation. It never
        holds a secret.
        """
        ...

    def restore_effect(
        self,
        configuration: ProviderConfiguration,
        server: Server,
        backup: Backup,
    ) -> RestoreEffect:
        """Return what restoring ``backup`` over ``server`` does.

        The text states what is replaced or deleted and that it cannot be
        undone; ``deleted_backups`` holds the backups the restore deletes.
        """
        ...

    def request_backup(
        self,
        configuration: ProviderConfiguration,
        server: Server,
        storage: str | None,
    ) -> Operation:
        """Send the one backup request.

        The operation identifier is put into the marker or description the
        provider keeps, so the backup can be found again. Returns the Operation
        with action BACKUP in state RUNNING; in state FAILED with the reason as
        its detail when the provider rejects the request (authentication,
        permission, rate limit, 4xx); or in state UNKNOWN when the answer was
        lost (timeout, 5xx) and the request may have reached the provider. The
        request is never repeated.

        Raises BackupUnsupportedError when the provider does not declare the
        capability ``backup_server`` (no request is sent).
        """
        ...

    def request_restore(
        self,
        configuration: ProviderConfiguration,
        server: Server,
        backup: Backup,
    ) -> Operation:
        """Send the one restore request.

        Returns the Operation with action RESTORE, as ``request_backup`` does.
        The caller checks ``restore_block`` first; a request that would restore a
        running guest or a backup of another guest is not sent and gives a FAILED
        operation.

        Raises BackupUnsupportedError when the provider does not declare the
        capability ``restore_server`` (no request is sent).
        """
        ...

    def list_backups(
        self, configuration: ProviderConfiguration, server: Server
    ) -> tuple[Backup, ...]:
        """Return the backups the provider holds for ``server``, oldest first."""
        ...

    def find_backup(
        self, configuration: ProviderConfiguration, operation: Operation
    ) -> Backup | None:
        """Return the backup that the BACKUP ``operation`` made, or None.

        None when the provider lists no backup that carries the operation's
        marker.
        """
        ...

    def operation_state(
        self, configuration: ProviderConfiguration, operation: Operation
    ) -> Operation:
        """Return ``operation`` updated with the provider's current view.

        One read, no waiting. An operation that has already ended is returned
        unchanged. Polling and the time limit belong to the caller.

        Raises a ProviderError subclass when the provider cannot be asked or
        no longer knows the task or the instance.
        """
        ...
