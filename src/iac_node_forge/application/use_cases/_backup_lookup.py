"""What the backup use cases share: the capability check and the choice of a backup."""

from collections.abc import Sequence

from iac_node_forge.application.backup_dto import BackupSummary
from iac_node_forge.application.ports import ProviderPort
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_record import BackupRecord
from iac_node_forge.domain.provider_configuration import ProviderConfiguration


def declares(
    provider: ProviderPort, target: ProviderConfiguration, capability: str
) -> bool:
    """Whether the provider declares the capability for this configuration."""
    return capability in {c.name for c in provider.discover_capabilities(target)}


def resolve_backup(listed: Sequence[Backup], text: str) -> Backup | None:
    """Return the backup named by its full identifier or by its unique alias.

    None when nothing is listed under ``text`` or the alias fits several
    backups. An identifier always wins over an alias.
    """
    wanted = text.strip()
    exact = [backup for backup in listed if backup.backup_id == wanted]
    if exact:
        return exact[0]
    by_alias = [backup for backup in listed if backup.alias() == wanted.lower()]
    return by_alias[0] if len(by_alias) == 1 else None


def summary_of_record(record: BackupRecord) -> BackupSummary:
    """Return the summary of a recorded backup: what the record kept of it."""
    kept = Backup(
        backup_id=record.backup_id,
        kind=record.kind,
        location=record.location,
        created_at=record.created_at,
        made_by_client=True,
    )
    return BackupSummary.of(kept, record.operation_id)
