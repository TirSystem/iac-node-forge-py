"""The boundary to the local disk: room, manifests, destination and report."""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from iac_node_forge.domain.manifest import Manifest
from iac_node_forge.domain.transfer_report import TransferReport


@runtime_checkable
class LocalFilesystemPort(Protocol):
    """What a transfer needs from the PC's disk."""

    def free_bytes(self, directory: Path) -> int:
        """Return the free bytes where ``directory`` is or would be created."""
        ...

    def is_new_or_empty(self, directory: Path) -> bool:
        """Return True when ``directory`` does not exist or is an empty directory."""
        ...

    def missing(self, paths: Sequence[Path]) -> list[Path]:
        """Return the paths that do not exist."""
        ...

    def size(self, paths: Sequence[Path]) -> int:
        """Return the bytes of the regular files in and at ``paths``."""
        ...

    def manifest_of_paths(self, paths: Sequence[Path]) -> Manifest:
        """Return the manifest of ``paths``, each listed by its final name."""
        ...

    def manifest_under(
        self, directory: Path, relative_paths: Sequence[str]
    ) -> Manifest:
        """Return the manifest of ``directory``/``relative_path`` for each path.

        Entry paths are relative to ``directory``. A path that does not exist
        lists nothing, so a comparison reports it as missing.
        """
        ...

    def save_report(self, directory: Path, report: TransferReport) -> str:
        """Write the report as a JSON file in ``directory``; return its path.

        The directory is created when it does not exist.
        """
        ...
