"""What a Proxmox VE backup and restore are made of: requests, texts and parsing.

Pure functions, no network. The provider sends the two requests this module
describes: ``POST /nodes/{node}/vzdump`` and the restore ``POST
/nodes/{node}/lxc|qemu`` with ``force`` (ADR-0010, B1, B10, B11). A dump never
prunes older backups (``remove=0``). The archive is found again by listing the
storage content, not through the task (B9), and the product's marker in the
notes is the second check.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from iac_node_forge.application.ports.server_backup_port import BackupBlock
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.server import Server

BACKUP_PERMISSION = "VM.Backup on the guest and Datastore.AllocateSpace on the storage"
"""What a dump needs (B14); named in a rejection, never a secret."""

RESTORE_PERMISSION = (
    "VM.Backup (or VM.Allocate) on the guest and Datastore.AllocateSpace on the storage"
)
"""What a restore over an existing guest needs (B14)."""

LISTING_PERMISSION = "Datastore.Audit or Datastore.AllocateSpace on the storage"
"""What listing the storages and their archives needs (B14)."""

MARKER = "iac-node-forge"
"""The words that start the notes of every archive this client makes."""

CONTENT_BACKUP = "backup"

RUNNING = "running"

# Longest name or volume identifier put into a consequence text, so that the
# whole text stays within the bound of an Operation's consequence.
_MAX_NAME = 40
_MAX_VOLID = 120
_VOLUME = re.compile(r"[^:/]+:backup/vzdump-(?P<type>lxc|qemu)-(?P<vmid>[0-9]+)-")

# JSON from the provider is untyped by nature; it is narrowed in this module.
type _Json = Any


@dataclass(frozen=True, slots=True)
class GuestRequest:
    """A write request: its path and the form fields it sends."""

    path: str
    form: Mapping[str, str]


def notes_template(operation_id: OperationId) -> str:
    """Return the notes of an archive: the marker, the operation, the guest name."""
    return f"{MARKER} {operation_id} {{{{guestname}}}}"


def dump_request(
    node: str, vmid: str, storage: str, operation_id: OperationId
) -> GuestRequest:
    """Return the dump of one guest to a storage, in snapshot mode, pruning nothing."""
    return GuestRequest(
        f"/nodes/{quote(node, safe='')}/vzdump",
        {
            "vmid": vmid,
            "storage": storage,
            "mode": "snapshot",
            "remove": "0",
            "notes-template": notes_template(operation_id),
        },
    )


def restore_request(node: str, guest_type: str, vmid: str, volid: str) -> GuestRequest:
    """Return the restore of an archive over the guest with the same vmid.

    A container takes the archive as ``ostemplate`` with ``restore=1``, a virtual
    machine as ``archive``; ``force`` lets either replace the existing guest.
    Neither ``start``, ``unique`` nor ``storage`` is sent.
    """
    path = f"/nodes/{quote(node, safe='')}/{guest_type}"
    if guest_type == "lxc":
        form = {"vmid": vmid, "ostemplate": volid, "restore": "1", "force": "1"}
    else:
        form = {"vmid": vmid, "archive": volid, "force": "1"}
    return GuestRequest(path, form)


def storages_path(node: str) -> str:
    """Return the listing of the storages of a node that accept backups."""
    return f"/nodes/{quote(node, safe='')}/storage?content={CONTENT_BACKUP}"


def content_path(node: str, storage: str, vmid: str) -> str:
    """Return the listing of the archives of one guest on one storage."""
    return (
        f"/nodes/{quote(node, safe='')}/storage/{quote(storage, safe='')}"
        f"/content?content={CONTENT_BACKUP}&vmid={quote(vmid, safe='')}"
    )


def storage_names(listing: list[_Json]) -> tuple[str, ...]:
    """Return the names of the storages whose content list holds ``backup``.

    The listing is already filtered by the provider; an entry that shows a
    content list without ``backup`` is dropped as well.
    """
    names: list[str] = []
    for item in listing:
        if not isinstance(item, Mapping) or not isinstance(item.get("storage"), str):
            continue
        content = item.get("content")
        if isinstance(content, str) and CONTENT_BACKUP not in content.split(","):
            continue
        names.append(str(item["storage"]))
    return tuple(names)


def backup_block(storages: tuple[str, ...], storage: str | None) -> BackupBlock | None:
    """Return why a backup cannot be made on ``storage`` (None: none was named).

    Several storages with none named is not a block: the caller asks the user.
    """
    if storage is None:
        return None if storages else BackupBlock.NO_BACKUP_STORAGE
    return None if storage in storages else BackupBlock.STORAGE_UNSUITABLE


def restore_block(
    server: Server, guest_type: str, backup: Backup
) -> BackupBlock | None:
    """Return why ``backup`` cannot be restored over ``server`` now.

    A running guest is refused and never stopped for the user (B12); an archive
    of another type of guest, or of another vmid, is a mismatch.
    """
    if server.state == RUNNING:
        return BackupBlock.RUNNING
    archived = guest_of_volume(backup.backup_id)
    if backup.guest_type not in (None, guest_type):
        return BackupBlock.MISMATCH
    if archived is not None and archived != (guest_type, server.server_identifier):
        return BackupBlock.MISMATCH
    return None


def guest_of_volume(volid: str) -> tuple[str, str] | None:
    """Return the guest type and vmid a ``vzdump-...`` volume name encodes (B8)."""
    match = _VOLUME.match(volid)
    return (match["type"], match["vmid"]) if match else None


def has_marker(notes: str | None) -> bool:
    """Whether the notes start with the marker of this client."""
    return notes is not None and notes.startswith(f"{MARKER} ")


def carries_operation(notes: str | None, operation_id: OperationId) -> bool:
    """Whether the notes carry the marker of this client and this operation."""
    return notes is not None and notes.startswith(f"{MARKER} {operation_id}")


def to_backup(item: Mapping[str, _Json], vmid: str, storage: str) -> Backup | None:
    """Return the archive of guest ``vmid`` an entry of a storage lists, or None.

    An entry of another content type or guest, or without a usable ``volid`` or
    ``ctime``, is not a backup this client can describe.
    """
    volid = item.get("volid")
    created = _timestamp(item.get("ctime"))
    if not isinstance(volid, str) or not volid.strip() or created is None:
        return None
    if item.get("content", CONTENT_BACKUP) != CONTENT_BACKUP:
        return None
    guest = guest_of_volume(volid)
    if str(item.get("vmid", vmid)) != vmid or (guest and guest[1] != vmid):
        return None
    notes = item.get("notes")
    label = _first_line(notes) if isinstance(notes, str) else None
    size = item.get("size")
    return Backup(
        backup_id=volid,
        kind=BackupKind.ARCHIVE,
        location=f"storage {storage}, volume {volid}",
        created_at=created,
        size_bytes=size if _is_count(size) else None,
        label=label,
        guest_type=guest[0] if guest else None,
        made_by_client=has_marker(label),
    )


def backup_consequence(server: Server, node: str, storage: str) -> str:
    """State what a backup does: an archive on a storage, nothing else changed."""
    return (
        f"This writes a backup of guest {server.server_identifier} "
        f"({_short(server.name, _MAX_NAME)}) on node {node} to storage {storage} as "
        "an archive file, made while the guest runs. It changes nothing on the "
        "guest, uses space on the storage and deletes no earlier backup. The "
        "archive is on the storage, not off-site."
    )


def restore_consequence(server: Server, node: str, backup: Backup) -> str:
    """State what a restore does: the guest is overwritten, for good."""
    made = backup.created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        f"Guest {server.server_identifier} ({_short(server.name, _MAX_NAME)}) on "
        f"node {node} is overwritten by the backup "
        f"{_short(backup.backup_id, _MAX_VOLID)} made at {made}: its disks and its "
        "configuration are replaced by the content of the archive. What force "
        "leaves untouched is not documented. This cannot be undone."
    )


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _first_line(notes: str) -> str | None:
    lines = notes.strip().splitlines()
    return lines[0].strip() or None if lines else None


def _timestamp(value: _Json) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _is_count(value: _Json) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
