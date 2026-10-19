"""The list of files and links of a tree, with a hash, and how two are compared."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from iac_node_forge.domain.errors import InvalidManifestError
from iac_node_forge.domain.remote_path import validate_relative_path

_SHA256 = re.compile(r"[0-9a-f]{64}")


class EntryKind(StrEnum):
    """What a manifest lists: a regular file or a symbolic link."""

    FILE = "file"
    LINK = "link"


class DifferenceKind(StrEnum):
    """How a copy differs from its source."""

    MISSING = "missing"
    EXTRA = "extra"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """A file (size and SHA-256) or a link (its target) at a relative path.

    A link has no size of its own, so its size is 0.
    """

    path: str
    kind: EntryKind
    size: int
    checksum: str | None
    link_target: str | None

    def __post_init__(self) -> None:
        validate_relative_path("ManifestEntry.path", self.path)
        if self.size < 0:
            raise InvalidManifestError("ManifestEntry.size must not be negative")
        if self.kind is EntryKind.FILE:
            self._require_file()
        else:
            self._require_link()

    def _require_file(self) -> None:
        if self.checksum is None or _SHA256.fullmatch(self.checksum) is None:
            raise InvalidManifestError("a file entry needs a lower case SHA-256")
        if self.link_target is not None:
            raise InvalidManifestError("a file entry has no link target")

    def _require_link(self) -> None:
        if self.link_target is None or self.checksum is not None or self.size != 0:
            raise InvalidManifestError(
                "a link entry needs a target and has no checksum and no size"
            )


@dataclass(frozen=True, slots=True)
class ManifestDifference:
    """One path in which a copy differs from its source."""

    path: str
    kind: DifferenceKind


@dataclass(frozen=True, slots=True)
class Manifest:
    """Immutable entries, ordered by path, one per path."""

    entries: tuple[ManifestEntry, ...]

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.entries, key=lambda entry: entry.path))
        paths = [entry.path for entry in ordered]
        if len(set(paths)) != len(paths):
            raise InvalidManifestError("a manifest lists a path twice")
        object.__setattr__(self, "entries", ordered)

    @classmethod
    def of(cls, entries: Iterable[ManifestEntry]) -> "Manifest":
        """Build a manifest from entries in any order."""
        return cls(tuple(entries))

    @property
    def file_count(self) -> int:
        """Return the number of regular files."""
        return sum(1 for entry in self.entries if entry.kind is EntryKind.FILE)

    @property
    def link_count(self) -> int:
        """Return the number of symbolic links."""
        return sum(1 for entry in self.entries if entry.kind is EntryKind.LINK)

    @property
    def byte_count(self) -> int:
        """Return the total size of the regular files."""
        return sum(entry.size for entry in self.entries)

    def without(self, paths: Iterable[str]) -> "Manifest":
        """Return the manifest without the entries at or under ``paths``."""
        prefixes = tuple(paths)
        return Manifest(
            tuple(
                entry
                for entry in self.entries
                if not any(
                    entry.path == prefix or entry.path.startswith(prefix + "/")
                    for prefix in prefixes
                )
            )
        )

    def differences_in(self, copy: "Manifest") -> tuple[ManifestDifference, ...]:
        """Return how ``copy`` differs from this manifest (the source).

        A path only here is MISSING, a path only in the copy is EXTRA and a path
        in both with another kind, size, hash or target is CHANGED. The result
        is empty only when the copy is equal.
        """
        mine = {entry.path: entry for entry in self.entries}
        theirs = {entry.path: entry for entry in copy.entries}
        found = [
            ManifestDifference(path, DifferenceKind.MISSING)
            for path in mine.keys() - theirs.keys()
        ]
        found += [
            ManifestDifference(path, DifferenceKind.EXTRA)
            for path in theirs.keys() - mine.keys()
        ]
        found += [
            ManifestDifference(path, DifferenceKind.CHANGED)
            for path in mine.keys() & theirs.keys()
            if mine[path] != theirs[path]
        ]
        return tuple(sorted(found, key=lambda difference: difference.path))
