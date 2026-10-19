"""Unpacking and packing the tar streams of a transfer, in memory-light stream mode.

Unpacking trusts nothing the server sends: a name must be relative, free of
``..`` and under a requested path, and the ``data`` extraction filter refuses
links that point outside the destination, devices and set-user-id bits.
"""

import os
import stat
import tarfile
from collections.abc import Sequence
from pathlib import Path
from typing import IO

from iac_node_forge.application.errors import TransferFailedError, UnsafeArchiveError
from iac_node_forge.domain.transfer_report import SkippedEntry


def unpack(
    stream: IO[bytes], destination: Path, names: Sequence[str]
) -> list[SkippedEntry]:
    """Extract a tar stream under ``destination``; return what was skipped.

    ``names`` are the requested paths without a leading slash. Devices and pipes
    are skipped, and so is a member the extraction filter refuses; a skipped entry
    names the absolute path on the server. Raises
    UnsafeArchiveError for a name that is absolute, climbs out with ``..`` or is
    not under a requested path, and TransferFailedError when the stream is
    broken or a file cannot be written. The destination is created with the first
    member, so a stream that fails at once leaves nothing behind.
    """
    mask = _current_umask()
    skipped: list[SkippedEntry] = []
    try:
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for member in archive:
                _require_requested(member.name, names)
                reason = _special_reason(member)
                if reason is not None:
                    skipped.append(SkippedEntry(_remote_path(member), reason))
                    continue
                _extract(archive, member, destination, mask, skipped)
    except (tarfile.TarError, EOFError, OSError, KeyError) as error:
        raise TransferFailedError(f"the archive stream is broken: {error}") from error
    return skipped


def pack(stream: IO[bytes], local_paths: Sequence[Path]) -> list[SkippedEntry]:
    """Write a tar stream of ``local_paths``, each under its final name.

    Links are written as links and never followed; sockets, devices and pipes are
    skipped and returned. Owners are not recorded: the server's tar decides.
    """
    skipped: list[SkippedEntry] = []
    with tarfile.open(
        fileobj=stream, mode="w|", format=tarfile.GNU_FORMAT, encoding="utf-8"
    ) as archive:
        for path in local_paths:
            name = path.name or path.resolve().name
            _add(archive, path, name, skipped)
    return skipped


def _extract(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    destination: Path,
    mask: int,
    skipped: list[SkippedEntry],
) -> None:
    def keep_umask(info: tarfile.TarInfo, path: str) -> tarfile.TarInfo | None:
        filtered = tarfile.data_filter(info, path)
        if filtered.mode is not None:
            filtered = filtered.replace(mode=filtered.mode & ~mask, deep=False)
        return filtered

    try:
        archive.extract(member, destination, filter=keep_umask)
    except tarfile.FilterError as error:
        skipped.append(
            SkippedEntry(_remote_path(member), f"refused: {_filter_reason(error)}")
        )


def _remote_path(member: tarfile.TarInfo) -> str:
    return "/" + member.name


def _filter_reason(error: tarfile.FilterError) -> str:
    if isinstance(error, tarfile.AbsoluteLinkError):
        return "absolute link target"
    if isinstance(error, tarfile.LinkOutsideDestinationError):
        return "link points outside the destination"
    if isinstance(error, tarfile.SpecialFileError):
        return "special file"
    return "refused by the extraction filter"


def _require_requested(name: str, names: Sequence[str]) -> None:
    parts = name.split("/")
    if name.startswith("/") or ".." in parts:
        raise UnsafeArchiveError("the server sent an absolute or '..' member name")
    if not any(name == wanted or name.startswith(wanted + "/") for wanted in names):
        raise UnsafeArchiveError("the server sent a member that was not requested")


def _special_reason(member: tarfile.TarInfo) -> str | None:
    # isdev() is also true for a pipe, so the pipe is asked first.
    if member.isfifo():
        return "named pipe"
    if member.isdev():
        return "device"
    return None


def _current_umask() -> int:
    current = os.umask(0o022)
    os.umask(current)
    return current


def _add(
    archive: tarfile.TarFile, path: Path, name: str, skipped: list[SkippedEntry]
) -> None:
    mode = path.lstat().st_mode
    reason = _local_special_reason(mode)
    if reason is not None:
        skipped.append(SkippedEntry(str(path), reason))
        return
    archive.add(path, arcname=name, recursive=False, filter=_without_owner)
    if stat.S_ISDIR(mode):
        for child in sorted(path.iterdir()):
            _add(archive, child, f"{name}/{child.name}", skipped)


def _local_special_reason(mode: int) -> str | None:
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
        return "device"
    if stat.S_ISFIFO(mode):
        return "named pipe"
    return None


def _without_owner(info: tarfile.TarInfo) -> tarfile.TarInfo:
    return info.replace(uid=0, gid=0, uname="", gname="", deep=False)
