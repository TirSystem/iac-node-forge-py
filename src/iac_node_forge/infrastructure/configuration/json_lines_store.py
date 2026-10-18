"""ConfigurationStore kept in a JSON Lines file (ERD-001, ADR-0003).

The file format is in ``_json_lines_file``; the same file also holds the
operation records of ``JsonLinesOperationStore``, which this store keeps intact.
"""

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.infrastructure.configuration._json_lines_file import (
    Snapshot,
    read_snapshot,
    write_snapshot,
)

CONFIG_ENV_VAR = "IAC_NODE_FORGE_CONFIG"
_APP_FOLDER = "iac-node-forge"
_FILE_NAME = "configuration.jsonl"


def default_configuration_path(
    environment: Mapping[str, str], platform_name: str, home: Path | None = None
) -> Path:
    """Return where the configuration file lives for this environment.

    ``platform_name`` is ``sys.platform``-like: ``win32`` selects Windows rules.
    """
    override = environment.get(CONFIG_ENV_VAR)
    if override:
        return Path(override)
    if platform_name.startswith("win"):
        base = environment.get("APPDATA")
        root = Path(base) if base else (home or Path.home()) / "AppData" / "Roaming"
    else:
        xdg = environment.get("XDG_CONFIG_HOME")
        root = Path(xdg) if xdg else (home or Path.home()) / ".config"
    return root / _APP_FOLDER / _FILE_NAME


def _same_record(kept: ManagedServerRecord, record: ManagedServerRecord) -> bool:
    """Whether ``record`` is already kept: the same key, or an active record."""
    if (kept.configuration_id, kept.server_identifier) != (
        record.configuration_id,
        record.server_identifier,
    ):
        return False
    return not kept.is_ended or kept.recorded_at == record.recorded_at


class JsonLinesConfigurationStore:
    """Keeps configurations and managed servers in one JSON Lines file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, configuration: ProviderConfiguration) -> None:
        """Insert or replace the configuration and its credential reference."""
        snapshot = self._read()
        snapshot.configurations[configuration.configuration_id] = configuration
        reference = configuration.credential_reference
        if reference is not None:
            snapshot.references[reference.name] = reference
        self._write(snapshot)

    def load_all(self) -> tuple[ProviderConfiguration, ...]:
        """Return every saved configuration."""
        return tuple(self._read().configurations.values())

    def managed_servers(
        self, configuration_id: ConfigurationId
    ) -> tuple[ManagedServerRecord, ...]:
        """Return the servers managed under one configuration, not the ended."""
        return tuple(
            r
            for r in self._read().managed
            if r.configuration_id == configuration_id and not r.is_ended
        )

    def save_managed_server(self, record: ManagedServerRecord) -> None:
        """Record a managed server; one that is already managed is not written twice.

        A server whose earlier record is ended is recorded anew, because the
        provider may reuse its identifier.
        """
        snapshot = self._read()
        if any(_same_record(r, record) for r in snapshot.managed):
            return
        snapshot.managed.append(record)
        self._write(snapshot)

    def end_managed_server(
        self,
        configuration_id: ConfigurationId,
        server_identifier: str,
        ended_at: datetime,
        operation_id: OperationId,
    ) -> None:
        """End the active record of a server; nothing changes when there is none."""
        snapshot = self._read()
        for index, record in enumerate(snapshot.managed):
            if (
                record.configuration_id == configuration_id
                and record.server_identifier == server_identifier
                and not record.is_ended
            ):
                snapshot.managed[index] = record.ended(ended_at, operation_id)
                self._write(snapshot)
                return

    def _read(self) -> Snapshot:
        return read_snapshot(self._path)

    def _write(self, snapshot: Snapshot) -> None:
        write_snapshot(self._path, snapshot)
