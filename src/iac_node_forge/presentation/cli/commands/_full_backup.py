"""What ``server full-backup|full-backups|full-restore`` share (ADR-0010).

The wiring of the three use cases and the way a backup is shown. The commands
are called ``full-`` because ``server backup`` and ``server restore`` copy files
(ADR-0009).
"""

from typing import TextIO

from iac_node_forge.application.backup_dto import BackupSummary
from iac_node_forge.application.use_cases import (
    BackUpServer,
    ListBackups,
    RestoreServer,
)
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.provider_resolver import CliProviderResolver
from iac_node_forge.presentation.cli.services import CliServices

PROGRAM = "iac-node-forge server"

SNAPSHOT_NOTE = (
    "A snapshot is not an independent backup: the provider keeps it on the same "
    "disk system as the server."
)
"""Said wherever a snapshot is shown (ADR-0010); never for an archive."""

_BYTES_PER_MIB = 1024 * 1024


def build_back_up_server(
    services: CliServices, resolver: CliProviderResolver
) -> BackUpServer:
    """Wire BackUpServer for a command."""
    return BackUpServer(
        resolver, services.configurations, services.operations, services.backups
    )


def build_list_backups(
    services: CliServices, resolver: CliProviderResolver
) -> ListBackups:
    """Wire ListBackups for a command."""
    return ListBackups(resolver, services.configurations, services.backups)


def build_restore_server(
    services: CliServices, resolver: CliProviderResolver
) -> RestoreServer:
    """Wire RestoreServer for a command."""
    return RestoreServer(resolver, services.configurations, services.operations)


def kind_note(kind: BackupKind | None) -> str | None:
    """Return the sentence that qualifies the kind, or None for an archive."""
    return SNAPSHOT_NOTE if kind is BackupKind.SNAPSHOT else None


def backup_lines(backup: BackupSummary, indent: str = "  ") -> tuple[str, ...]:
    """Return the two lines that show a backup: who and when, then where."""
    made = "made by this client" if backup.made_by_client else "not made by this client"
    first = (
        f"{indent}{backup.alias}  {backup.created_at.isoformat()}  "
        f"{backup.kind.value}  {made}  {backup.backup_id}"
    )
    facts = [backup.location]
    if backup.size_bytes is not None:
        facts.append(f"{backup.size_bytes / _BYTES_PER_MIB:.1f} MiB")
    if backup.auto_delete_at is not None:
        facts.append(f"deleted by the provider on {backup.auto_delete_at.isoformat()}")
    if backup.operation_id is not None:
        facts.append(f"operation {backup.operation_id}")
    return first, f"{indent}    " + "; ".join(facts)


def write_backup(stream: TextIO, backup: BackupSummary, indent: str = "  ") -> None:
    """Write a backup as ``backup_lines`` shows it."""
    for line in backup_lines(backup, indent):
        write_line(stream, line)
