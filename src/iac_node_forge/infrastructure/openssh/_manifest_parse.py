"""Reading the output of the remote ``find`` commands into a manifest.

The parse is strict: a line it does not understand fails the whole manifest, so
a name with a line break or a changed tree can make a verification fail but
never make a different tree look equal.
"""

import re

from iac_node_forge.application.errors import TransferFailedError
from iac_node_forge.domain.manifest import EntryKind, Manifest, ManifestEntry

_HASH_LINE = re.compile(r"(\\?)([0-9a-f]{64}) [ *](.+)", re.DOTALL)
_SIZE_LINE = re.compile(r"(\d+) (.+)", re.DOTALL)
_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
_UNREADABLE = "the manifest output of the server could not be read"


def parse_hashes(output: str, root: str) -> dict[str, str]:
    """Return the SHA-256 of each file, by path relative to ``root``.

    ``sha256sum`` of GNU coreutils starts a line with a backslash and escapes
    ``\\`` and a line break in the name; BusyBox does not escape.
    """
    hashes: dict[str, str] = {}
    for line in _lines(output):
        match = _HASH_LINE.fullmatch(line)
        if match is None:
            raise TransferFailedError(_UNREADABLE)
        name = _unescape(match.group(3)) if match.group(1) else match.group(3)
        hashes[_relative(name, root)] = match.group(2)
    return hashes


def parse_sizes(output: str, root: str) -> dict[str, int]:
    """Return the size of each file, by path relative to ``root``."""
    sizes: dict[str, int] = {}
    for line in _lines(output):
        match = _SIZE_LINE.fullmatch(line)
        if match is None:
            raise TransferFailedError(_UNREADABLE)
        sizes[_relative(match.group(2), root)] = int(match.group(1))
    return sizes


def parse_links(output: str, root: str) -> dict[str, str]:
    """Return the target of each link, by path relative to ``root``.

    The lines come in pairs: the name of the link, then its target.
    """
    lines = _lines(output)
    if len(lines) % 2 != 0:
        raise TransferFailedError(_UNREADABLE)
    return {
        _relative(lines[index], root): lines[index + 1]
        for index in range(0, len(lines), 2)
    }


def build_manifest(
    hashes: dict[str, str], sizes: dict[str, int], links: dict[str, str]
) -> Manifest:
    """Join the three listings into a manifest.

    Raises TransferFailedError when a file has a hash and no size, or the other
    way round: the tree changed while it was read.
    """
    if hashes.keys() != sizes.keys() or hashes.keys() & links.keys():
        raise TransferFailedError("the tree changed while its manifest was read")
    entries = [
        ManifestEntry(path, EntryKind.FILE, sizes[path], checksum, None)
        for path, checksum in hashes.items()
    ]
    entries += [
        ManifestEntry(path, EntryKind.LINK, 0, None, target)
        for path, target in links.items()
    ]
    return Manifest.of(entries)


def _lines(output: str) -> list[str]:
    # Not splitlines(): it also splits at characters a file name may hold.
    lines = output.split("\n")
    return lines[:-1] if lines and lines[-1] == "" else lines


def _unescape(name: str) -> str:
    return _ESCAPE.sub(lambda m: "\n" if m.group(1) == "n" else m.group(1), name)


def _relative(name: str, root: str) -> str:
    prefix = root.rstrip("/") + "/"
    if not name.startswith(prefix) or name == prefix:
        raise TransferFailedError(_UNREADABLE)
    return name[len(prefix) :]
