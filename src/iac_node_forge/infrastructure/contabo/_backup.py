"""What a Contabo backup and restore are made of: requests, texts and parsing.

Pure functions, no network. Contabo has no backup interface (B22); the nearest
thing is a snapshot of the instance. The provider sends the two requests this
module describes: ``POST /v1/compute/instances/{id}/snapshots`` and ``POST
.../snapshots/{snapshotId}/rollback`` with an empty body (ADR-0010, B17, B18).
Neither has a task or a status (B20), so their end is read from the instance
status and the snapshot list. A rollback deletes every newer snapshot (B18).
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from iac_node_forge.application.ports.server_backup_port import BackupBlock
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.server import Server

NAME_PREFIX = "iacnf-"
"""The start of the name of every snapshot this client makes (18 characters in
all, within the limit of 30, B17)."""

STATUS_CANCELLED = "cancelled"
FINAL_STATUSES = frozenset({"running", "stopped"})
"""The instance statuses in which a snapshot or rollback may be judged done; the
others (``installing``, ``error`` ...) are not final."""

# How many newer snapshots a consequence text names before it says "and N more",
# so the whole text stays within the bound of an Operation's consequence.
_MAX_NAMED = 3
_MAX_NAME = 40

# JSON from the provider is untyped by nature; it is narrowed in this module.
type _Json = Any


def snapshots_path(instances_path: str, instance_id: int) -> str:
    """Return the path of the snapshots of one instance."""
    return f"{instances_path}/{instance_id}/snapshots"


def rollback_path(instances_path: str, instance_id: int, snapshot_id: str) -> str:
    """Return the path of the rollback of one instance to one snapshot."""
    return f"{snapshots_path(instances_path, instance_id)}/{snapshot_id}/rollback"


def snapshot_name(now: datetime) -> str:
    """Return ``iacnf-<UTC yyyymmddHHMM>`` for the time ``now``."""
    return NAME_PREFIX + now.astimezone(UTC).strftime("%Y%m%d%H%M")


def description(operation_id: OperationId) -> str:
    """Return the description that marks a snapshot as made by this operation."""
    return f"Created by iac-node-forge, operation {operation_id}"


def snapshot_body(now: datetime, operation_id: OperationId) -> dict[str, str]:
    """Return the body of a snapshot creation."""
    return {"name": snapshot_name(now), "description": description(operation_id)}


def carries_operation(item: Mapping[str, _Json], operation_id: OperationId) -> bool:
    """Whether a listed snapshot has the description of this operation."""
    text = item.get("description")
    return isinstance(text, str) and text.strip() == description(operation_id)


def snapshot_id(item: Mapping[str, _Json]) -> str | None:
    """Return the ``snapshotId`` of a snapshot, as text, or None."""
    value = item.get("snapshotId")
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def is_undated(item: Mapping[str, _Json]) -> bool:
    """Whether a listed snapshot has no usable ``createdDate``."""
    return _timestamp(item.get("createdDate")) is None


def to_backup(item: Mapping[str, _Json]) -> Backup | None:
    """Return the snapshot an entry lists, or None when it has no id or date."""
    identifier = snapshot_id(item)
    created = _timestamp(item.get("createdDate"))
    if identifier is None or created is None:
        return None
    name = item.get("name")
    label = name.strip() if isinstance(name, str) and name.strip() else None
    return Backup(
        backup_id=identifier,
        kind=BackupKind.SNAPSHOT,
        location=f"snapshot {label or identifier} at Contabo",
        created_at=created,
        label=label,
        made_by_client=label is not None and label.startswith(NAME_PREFIX),
        auto_delete_at=_timestamp(item.get("autoDeleteDate")),
    )


def newer_than(backup: Backup, listed: Sequence[Backup]) -> tuple[Backup, ...]:
    """Return the listed snapshots made after ``backup``, oldest first.

    These are the ones a rollback to ``backup`` deletes (B18).
    """
    newer = [
        other
        for other in listed
        if other.backup_id != backup.backup_id and other.created_at > backup.created_at
    ]
    return tuple(sorted(newer, key=lambda other: (other.created_at, other.backup_id)))


def backup_block(server: Server, storage: str | None) -> BackupBlock | None:
    """Return STORAGE_UNSUITABLE for a named storage (Contabo has none) and
    CANCELLED for a cancelled instance."""
    if storage is not None:
        return BackupBlock.STORAGE_UNSUITABLE
    return BackupBlock.CANCELLED if server.state == STATUS_CANCELLED else None


def backup_consequence(server: Server) -> str:
    """State what a backup is at Contabo: a snapshot, not an independent copy."""
    return (
        f"This creates a snapshot of instance {server.server_identifier} "
        f"({_short(server.name)}) at Contabo. A snapshot is not an independent "
        "backup: Contabo keeps it on the same disk system as the instance, counts "
        "it against the number of snapshots included in the product and deletes "
        "it on its auto-delete date. Cost is not stated. Contabo's paid "
        "automated-backup add-on exists but cannot be used through this client."
    )


def restore_consequence(
    server: Server, backup: Backup, deleted: Sequence[Backup]
) -> str:
    """State what a rollback does: the newer snapshots are deleted, for good."""
    return (
        f"Instance {server.server_identifier} ({_short(server.name)}) is rolled "
        f"back to snapshot {_short(backup.label or backup.backup_id)} "
        f"({_date(backup.created_at)}) and returns to the state it had then. "
        f"{_deleted_text(deleted)} Cost is not stated. This cannot be undone."
    )


def restored_detail(status: str) -> str:
    """Say that the rollback is judged done and in which status."""
    return f"the instance is {status} and no newer snapshot is listed"


def _deleted_text(deleted: Sequence[Backup]) -> str:
    if not deleted:
        return "No newer snapshot exists, so none is deleted."
    named = ", ".join(
        f"{_short(other.label or other.backup_id)} ({_date(other.created_at)})"
        for other in deleted[:_MAX_NAMED]
    )
    more = len(deleted) - _MAX_NAMED
    tail = f", and {more} more" if more > 0 else ""
    return f"The rollback deletes these newer snapshots: {named}{tail}."


def _date(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _short(text: str) -> str:
    return text if len(text) <= _MAX_NAME else text[: _MAX_NAME - 3] + "..."


def _timestamp(value: _Json) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
