"""ConfigurationStore kept in a JSON Lines file (ERD-001, ADR-0003).

One JSON object per line; ``record_type`` names the record. No record has a
field that can hold a secret: a configuration only names its credential
reference. Unknown record types and unknown fields are rejected on read.
"""

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from iac_node_forge.application.errors import ConfigurationFormatError
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.errors import DomainError
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind

CONFIG_ENV_VAR = "IAC_NODE_FORGE_CONFIG"
_APP_FOLDER = "iac-node-forge"
_FILE_NAME = "configuration.jsonl"
_OWNER_ONLY = 0o600

_PROVIDER = "provider_configuration"
_REFERENCE = "credential_reference"
_MANAGED = "managed_server"
_FIELDS: dict[str, frozenset[str]] = {
    _PROVIDER: frozenset(
        {
            "record_type",
            "configuration_id",
            "provider_kind",
            "display_name",
            "address",
            "credential_reference_name",
        }
    ),
    _REFERENCE: frozenset({"record_type", "name", "store_kind"}),
    _MANAGED: frozenset(
        {"record_type", "configuration_id", "server_identifier", "recorded_at"}
    ),
}

type _Row = dict[str, str | None]


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


@dataclass(slots=True)
class _Snapshot:
    configurations: dict[ConfigurationId, ProviderConfiguration] = field(
        default_factory=dict
    )
    references: dict[str, CredentialReference] = field(default_factory=dict)
    managed: list[ManagedServerRecord] = field(default_factory=list)


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
        """Return the servers managed under one configuration."""
        return tuple(
            r for r in self._read().managed if r.configuration_id == configuration_id
        )

    def _read(self) -> _Snapshot:
        if not self._path.exists():
            return _Snapshot()
        text = self._path.read_text(encoding="utf-8")
        rows = [
            _parse_line(number, line)
            for number, line in enumerate(text.splitlines(), start=1)
            if line.strip()
        ]
        return _build(rows)

    def _write(self, snapshot: _Snapshot) -> None:
        rows = [_provider_row(c) for c in snapshot.configurations.values()]
        rows += [_reference_row(r) for r in snapshot.references.values()]
        rows += [_managed_row(m) for m in snapshot.managed]
        payload = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(self._path, payload)


def _write_atomically(path: Path, payload: str) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        if os.name == "posix":
            os.chmod(temp_name, _OWNER_ONLY)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _provider_row(configuration: ProviderConfiguration) -> _Row:
    reference = configuration.credential_reference
    return {
        "record_type": _PROVIDER,
        "configuration_id": configuration.configuration_id,
        "provider_kind": configuration.provider_kind.value,
        "display_name": configuration.display_name,
        "address": configuration.address,
        "credential_reference_name": reference.name if reference else None,
    }


def _reference_row(reference: CredentialReference) -> _Row:
    return {
        "record_type": _REFERENCE,
        "name": reference.name,
        "store_kind": reference.store_kind,
    }


def _managed_row(record: ManagedServerRecord) -> _Row:
    return {
        "record_type": _MANAGED,
        "configuration_id": record.configuration_id,
        "server_identifier": record.server_identifier,
        "recorded_at": record.recorded_at.isoformat(),
    }


def _parse_line(number: int, line: str) -> _Row:
    try:
        row = json.loads(line)
    except json.JSONDecodeError as error:
        raise ConfigurationFormatError(f"line {number}: not valid JSON") from error
    if not isinstance(row, dict):
        raise ConfigurationFormatError(f"line {number}: not a JSON object")
    record_type = row.get("record_type")
    if not isinstance(record_type, str) or record_type not in _FIELDS:
        raise ConfigurationFormatError(f"line {number}: unknown record type")
    expected = _FIELDS[record_type]
    unknown = sorted(str(key) for key in set(row) - expected)
    if unknown:
        # Names only: a value could be a secret someone pasted in.
        raise ConfigurationFormatError(
            f"line {number}: unknown field {', '.join(map(repr, unknown))}"
        )
    missing = sorted(expected - set(row))
    if missing:
        raise ConfigurationFormatError(
            f"line {number}: missing field {', '.join(map(repr, missing))}"
        )
    for key, value in row.items():
        nullable = key == "credential_reference_name"
        if not (isinstance(value, str) or (nullable and value is None)):
            raise ConfigurationFormatError(f"line {number}: field {key!r} not text")
    return row


def _build(rows: list[_Row]) -> _Snapshot:
    snapshot = _Snapshot()
    try:
        _collect(rows, snapshot)
    except (DomainError, ValueError) as error:
        raise ConfigurationFormatError(f"invalid record: {error}") from error
    return snapshot


def _collect(rows: list[_Row], snapshot: _Snapshot) -> None:
    for row in rows:
        if row["record_type"] == _REFERENCE:
            reference = CredentialReference(
                _text(row, "name"), _text(row, "store_kind")
            )
            snapshot.references[reference.name] = reference
    for row in rows:
        kind = row["record_type"]
        if kind == _PROVIDER:
            configuration = _configuration(row, snapshot.references)
            snapshot.configurations[configuration.configuration_id] = configuration
        elif kind == _MANAGED:
            snapshot.managed.append(
                ManagedServerRecord(
                    ConfigurationId(_text(row, "configuration_id")),
                    _text(row, "server_identifier"),
                    datetime.fromisoformat(_text(row, "recorded_at")),
                )
            )


def _configuration(
    row: _Row, references: dict[str, CredentialReference]
) -> ProviderConfiguration:
    name = row["credential_reference_name"]
    if name is not None and name not in references:
        raise ConfigurationFormatError("configuration names an unknown reference")
    return ProviderConfiguration(
        configuration_id=ConfigurationId(_text(row, "configuration_id")),
        provider_kind=ProviderKind(_text(row, "provider_kind")),
        display_name=_text(row, "display_name"),
        address=_text(row, "address"),
        credential_reference=references[name] if name is not None else None,
    )


def _text(row: _Row, key: str) -> str:
    value = row[key]
    if value is None:
        raise ConfigurationFormatError(f"field {key!r} must not be null")
    return value
