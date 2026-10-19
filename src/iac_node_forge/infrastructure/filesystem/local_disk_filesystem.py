"""LocalFilesystemPort on the PC's disk (ADR-0009).

Manifests are made with ``hashlib`` and ``os.readlink``; links are never
followed. Devices, sockets and pipes are not listed. Modes and owners are not
part of a manifest.
"""

import hashlib
import json
import os
import shutil
import stat
from collections.abc import Iterator, Sequence
from datetime import UTC
from pathlib import Path

from iac_node_forge.domain.manifest import EntryKind, Manifest, ManifestEntry
from iac_node_forge.domain.transfer_report import TransferReport

_CHUNK = 1 << 20


class LocalDiskFilesystem:
    """Room, manifests, the check of a destination and the report file."""

    def free_bytes(self, directory: Path) -> int:
        """Return the free bytes where ``directory`` is or would be created."""
        existing = directory
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        return shutil.disk_usage(existing).free

    def is_new_or_empty(self, directory: Path) -> bool:
        """Return True when ``directory`` does not exist or is empty."""
        if not directory.exists():
            return True
        return directory.is_dir() and not any(directory.iterdir())

    def missing(self, paths: Sequence[Path]) -> list[Path]:
        """Return the paths that do not exist (a dangling link exists)."""
        return [p for p in paths if not (p.exists() or p.is_symlink())]

    def size(self, paths: Sequence[Path]) -> int:
        """Return the bytes of the regular files in and at ``paths``."""
        return sum(
            path.lstat().st_size
            for top in paths
            for path in _walk(top)
            if _is_file(path)
        )

    def manifest_of_paths(self, paths: Sequence[Path]) -> Manifest:
        """Return the manifest of ``paths``, each listed by its final name."""
        entries: list[ManifestEntry] = []
        for top in paths:
            name = top.name or top.resolve().name
            entries += _entries(top, name)
        return Manifest.of(entries)

    def manifest_under(
        self, directory: Path, relative_paths: Sequence[str]
    ) -> Manifest:
        """Return the manifest of ``directory``/``relative_path`` for each path."""
        entries: list[ManifestEntry] = []
        for relative in relative_paths:
            entries += _entries(directory.joinpath(*relative.split("/")), relative)
        return Manifest.of(entries)

    def save_report(self, directory: Path, report: TransferReport) -> str:
        """Write the report as JSON in ``directory`` and return its path."""
        directory.mkdir(parents=True, exist_ok=True)
        path = _free_name(directory, report)
        content = json.dumps(_report_json(report, str(path)), indent=2) + "\n"
        path.write_text(content, encoding="utf-8", newline="\n")
        return str(path)


def _walk(top: Path) -> Iterator[Path]:
    """Yield ``top`` and everything below it, links not followed."""
    if not (top.exists() or top.is_symlink()):
        return
    yield top
    if stat.S_ISDIR(top.lstat().st_mode):
        for child in sorted(top.iterdir()):
            yield from _walk(child)


def _is_file(path: Path) -> bool:
    return stat.S_ISREG(path.lstat().st_mode)


def _entries(top: Path, name: str) -> Iterator[ManifestEntry]:
    """Yield the manifest entries at and below ``top``, named from ``name``."""
    for path in _walk(top):
        relative = path.relative_to(top).as_posix()
        entry_name = name if relative == "." else f"{name}/{relative}"
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            yield ManifestEntry(
                entry_name, EntryKind.LINK, 0, None, Path(os.readlink(path)).as_posix()
            )
        elif stat.S_ISREG(mode):
            yield ManifestEntry(
                entry_name, EntryKind.FILE, path.lstat().st_size, _sha256(path), None
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _free_name(directory: Path, report: TransferReport) -> Path:
    stamp = report.finished_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = f"transfer-report-{report.direction.value}-{stamp}"
    candidate = directory / f"{base}.json"
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = directory / f"{base}-{counter}.json"
    return candidate


def _report_json(report: TransferReport, report_file: str) -> dict[str, object]:
    return {
        "direction": report.direction.value,
        "configuration_id": report.configuration_id,
        "server_identifier": report.server_identifier,
        "started_at": report.started_at.isoformat(),
        "finished_at": report.finished_at.isoformat(),
        "file_count": report.file_count,
        "link_count": report.link_count,
        "byte_count": report.byte_count,
        "skipped": [{"path": s.path, "reason": s.reason} for s in report.skipped],
        "overwritten": [
            {"path": o.path, "size": o.size, "modified_at": o.modified_at.isoformat()}
            for o in report.overwritten
        ],
        "differences": [
            {"path": d.path, "kind": d.kind.value} for d in report.differences
        ],
        "verified": report.verified,
        "note": report.note,
        "report_file": report_file,
    }
