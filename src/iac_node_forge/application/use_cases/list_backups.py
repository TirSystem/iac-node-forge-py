"""UC-019: list the backups of a server, the choice of a backup to restore."""

from iac_node_forge.application.backup_dto import (
    BackupListing,
    BackupOutcome,
    BackupSummary,
)
from iac_node_forge.application.capabilities import BACKUP_SERVER
from iac_node_forge.application.errors import ServerNotFoundError
from iac_node_forge.application.ports import (
    BackupStore,
    ConfigurationStore,
    ProviderResolver,
)
from iac_node_forge.application.use_cases._backup_lookup import (
    declares,
    summary_of_record,
)
from iac_node_forge.application.use_cases._configuration_lookup import (
    find_configuration,
)


class ListBackups:
    """Lists what the provider holds for a server, merged with the records.

    The provider is the authority. A recorded backup that the provider no longer
    lists is reported apart as gone, and its record is not deleted. Nothing is
    written.
    """

    def __init__(
        self,
        providers: ProviderResolver,
        configurations: ConfigurationStore,
        backups: BackupStore,
    ) -> None:
        self._providers = providers
        self._configurations = configurations
        self._backups = backups

    def list_backups(self, configuration: str, server_identifier: str) -> BackupListing:
        """Return the backups of one server, oldest first.

        Each backup the provider lists carries the operation that made it when a
        record says so. Raises the use case and provider errors of the reads.
        """
        target = find_configuration(self._configurations, configuration)

        def stop(outcome: BackupOutcome, reason: str) -> BackupListing:
            return BackupListing(
                outcome,
                reason,
                target.display_name,
                target.configuration_id,
                server_identifier,
            )

        provider = self._providers.provider_for(target)
        if not declares(provider, target, BACKUP_SERVER):
            return stop(
                BackupOutcome.UNSUPPORTED,
                "the provider does not support backing up a server (capability "
                f"{BACKUP_SERVER})",
            )
        try:
            server = provider.get_server(target, server_identifier)
        except ServerNotFoundError:
            return stop(
                BackupOutcome.NOT_FOUND,
                f"the provider has no server {server_identifier!r}",
            )
        port = self._providers.backup_for(target)
        listed = port.list_backups(target, server)
        records = {
            record.backup_id: record
            for record in self._backups.load_for(
                target.configuration_id, server.server_identifier
            )
        }
        held = {backup.backup_id for backup in listed}
        return BackupListing(
            BackupOutcome.LISTED,
            "",
            target.display_name,
            target.configuration_id,
            server.server_identifier,
            port.backup_kind(target),
            tuple(
                BackupSummary.of(
                    backup,
                    records[backup.backup_id].operation_id
                    if backup.backup_id in records
                    else None,
                )
                for backup in listed
            ),
            tuple(
                summary_of_record(record)
                for record in records.values()
                if record.backup_id not in held
            ),
        )
