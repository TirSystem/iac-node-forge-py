"""The JSON Lines file of the client: records, reading and atomic writing.

One JSON object per line; ``record_type`` names the record. No record has a
field that can hold a secret: a configuration only names its credential
reference, and an operation holds identifiers, states, times and bounded
texts. Unknown record types and unknown fields are rejected on read.

The file is read whole and written whole, so the stores that share it
(configuration, operations, desired configurations and intents) keep each
other's records.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from iac_node_forge.application.errors import ConfigurationFormatError
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.errors import DomainError
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.operation import Operation, OperationId, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.provision_intent import IntentId, ProvisionIntent
from iac_node_forge.infrastructure.configuration import _provisioning_rows as prov
from iac_node_forge.infrastructure.configuration._rows import Row, text

_OWNER_ONLY = 0o600

_PROVIDER = "provider_configuration"
_REFERENCE = "credential_reference"
_MANAGED = "managed_server"
_OPERATION = "operation"
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
    _OPERATION: frozenset(
        {
            "record_type",
            "operation_id",
            "configuration_id",
            "server_identifier",
            "action",
            "state",
            "started_at",
            "finished_at",
            "provider_reference",
            "detail",
        }
    ),
    **prov.FIELDS,
}
_NULLABLE = frozenset(
    {
        "credential_reference_name",
        "finished_at",
        "provider_reference",
        "detail",
        "server_identifier",
    }
    | prov.NULLABLE
)


@dataclass(slots=True)
class Snapshot:
    """Every record of the file, by kind."""

    configurations: dict[ConfigurationId, ProviderConfiguration] = field(
        default_factory=dict
    )
    references: dict[str, CredentialReference] = field(default_factory=dict)
    managed: list[ManagedServerRecord] = field(default_factory=list)
    operations: dict[OperationId, Operation] = field(default_factory=dict)
    desired: dict[prov.DesiredKey, DesiredConfiguration] = field(default_factory=dict)
    intents: dict[IntentId, ProvisionIntent] = field(default_factory=dict)


def read_snapshot(path: Path) -> Snapshot:
    """Return the records of the file, or an empty snapshot when it is absent.

    Raises ConfigurationFormatError for a line the client cannot accept.
    """
    if not path.exists():
        return Snapshot()
    content = path.read_text(encoding="utf-8")
    rows = [
        _parse_line(number, line)
        for number, line in enumerate(content.splitlines(), start=1)
        if line.strip()
    ]
    return _build(rows)


def write_snapshot(path: Path, snapshot: Snapshot) -> None:
    """Replace the file with the records of ``snapshot``, atomically."""
    rows = [_provider_row(c) for c in snapshot.configurations.values()]
    rows += [_reference_row(r) for r in snapshot.references.values()]
    rows += [_managed_row(m) for m in snapshot.managed]
    rows += [_operation_row(o) for o in snapshot.operations.values()]
    for desired in snapshot.desired.values():
        rows += prov.desired_rows(desired)
    for intent in snapshot.intents.values():
        rows += prov.intent_rows(intent)
    payload = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomically(path, payload)


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


def _provider_row(configuration: ProviderConfiguration) -> Row:
    reference = configuration.credential_reference
    return {
        "record_type": _PROVIDER,
        "configuration_id": configuration.configuration_id,
        "provider_kind": configuration.provider_kind.value,
        "display_name": configuration.display_name,
        "address": configuration.address,
        "credential_reference_name": reference.name if reference else None,
    }


def _reference_row(reference: CredentialReference) -> Row:
    return {
        "record_type": _REFERENCE,
        "name": reference.name,
        "store_kind": reference.store_kind,
    }


def _managed_row(record: ManagedServerRecord) -> Row:
    return {
        "record_type": _MANAGED,
        "configuration_id": record.configuration_id,
        "server_identifier": record.server_identifier,
        "recorded_at": record.recorded_at.isoformat(),
    }


def _operation_row(operation: Operation) -> Row:
    finished_at = operation.finished_at
    return {
        "record_type": _OPERATION,
        "operation_id": operation.operation_id,
        "configuration_id": operation.configuration_id,
        "server_identifier": operation.server_identifier,
        "action": operation.action.value,
        "state": operation.state.value,
        "started_at": operation.started_at.isoformat(),
        "finished_at": finished_at.isoformat() if finished_at else None,
        "provider_reference": operation.provider_reference,
        "detail": operation.detail,
    }


def _parse_line(number: int, line: str) -> Row:
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
        if not (isinstance(value, str) or (key in _NULLABLE and value is None)):
            raise ConfigurationFormatError(f"line {number}: field {key!r} not text")
    return row


def _build(rows: list[Row]) -> Snapshot:
    snapshot = Snapshot()
    try:
        _collect(rows, snapshot)
    except (DomainError, ValueError) as error:
        raise ConfigurationFormatError(f"invalid record: {error}") from error
    return snapshot


def _collect(rows: list[Row], snapshot: Snapshot) -> None:
    for row in rows:
        if row["record_type"] == _REFERENCE:
            reference = CredentialReference(text(row, "name"), text(row, "store_kind"))
            snapshot.references[reference.name] = reference
    records = prov.ProvisioningRecords()
    for row in rows:
        kind = row["record_type"]
        if kind == _PROVIDER:
            configuration = _configuration(row, snapshot.references)
            snapshot.configurations[configuration.configuration_id] = configuration
        elif kind == _MANAGED:
            snapshot.managed.append(
                ManagedServerRecord(
                    ConfigurationId(text(row, "configuration_id")),
                    text(row, "server_identifier"),
                    datetime.fromisoformat(text(row, "recorded_at")),
                )
            )
        elif kind == _OPERATION:
            operation = _operation(row)
            snapshot.operations[operation.operation_id] = operation
        else:
            records.add(row)
    snapshot.desired = records.desired_configurations()
    snapshot.intents = records.intents()


def _configuration(
    row: Row, references: dict[str, CredentialReference]
) -> ProviderConfiguration:
    name = row["credential_reference_name"]
    if name is not None and name not in references:
        raise ConfigurationFormatError("configuration names an unknown reference")
    return ProviderConfiguration(
        configuration_id=ConfigurationId(text(row, "configuration_id")),
        provider_kind=ProviderKind(text(row, "provider_kind")),
        display_name=text(row, "display_name"),
        address=text(row, "address"),
        credential_reference=references[name] if name is not None else None,
    )


def _operation(row: Row) -> Operation:
    finished_at = row["finished_at"]
    return Operation(
        operation_id=OperationId(text(row, "operation_id")),
        configuration_id=ConfigurationId(text(row, "configuration_id")),
        server_identifier=row["server_identifier"],
        action=PowerAction(text(row, "action")),
        state=OperationState(text(row, "state")),
        started_at=datetime.fromisoformat(text(row, "started_at")),
        finished_at=datetime.fromisoformat(finished_at) if finished_at else None,
        provider_reference=row["provider_reference"],
        detail=row["detail"],
    )
