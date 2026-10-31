"""The ``backup`` records in the client's JSON Lines file (ERD-001, revision 7).

A record is keyed by (configuration, server, backup), holds no secret, is written
atomically beside the other records and is never deleted. An operation line
accepts the actions ``backup`` and ``restore``.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from iac_node_forge.application.errors import ConfigurationFormatError
from iac_node_forge.application.ports import BackupStore
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.backup_record import BackupRecord
from iac_node_forge.domain.operation import Operation, OperationId, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.infrastructure.configuration import (
    JsonLinesBackupStore,
    JsonLinesOperationStore,
    JsonLinesServerAccessStore,
)

LAB = ConfigurationId("lab")
OTHER = ConfigurationId("other")
CREATED = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
RECORD_FIELDS = {
    "record_type",
    "configuration_id",
    "server_identifier",
    "backup_id",
    "kind",
    "location",
    "created_at",
    "operation_id",
}


def _record(
    backup_id: str = "local:backup/vzdump-lxc-101-2026_10_20-10_00_00.tar",
    server_identifier: str = "101",
    kind: BackupKind = BackupKind.ARCHIVE,
    location: str = "storage local, volume vzdump-lxc-101-2026_10_20-10_00_00.tar",
    created_at: datetime = CREATED,
    operation_id: str = "op-1",
) -> BackupRecord:
    return BackupRecord(
        LAB,
        server_identifier,
        backup_id,
        kind,
        location,
        created_at,
        OperationId(operation_id),
    )


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def _operation(action: PowerAction) -> Operation:
    return Operation(
        OperationId("op-" + action.value),
        LAB,
        "101",
        action,
        OperationState.SUCCEEDED,
        CREATED,
        CREATED + timedelta(seconds=5),
        consequence="It cannot be undone.",
    )


def test_the_store_satisfies_the_port(tmp_path: Path) -> None:
    assert isinstance(JsonLinesBackupStore(tmp_path / "c.jsonl"), BackupStore)


def test_a_saved_record_is_loaded_with_every_field(tmp_path: Path) -> None:
    store = JsonLinesBackupStore(tmp_path / "c.jsonl")
    record = _record(kind=BackupKind.SNAPSHOT, backup_id="5d2c-1")

    store.save(record)

    assert store.load(LAB, "101", "5d2c-1") == record


def test_the_line_has_exactly_the_fields_of_the_record_and_no_secret(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesBackupStore(path).save(_record())

    [row] = _rows(path)

    assert set(row) == RECORD_FIELDS
    assert row["record_type"] == "backup"
    assert row["kind"] == "archive"
    assert row["created_at"] == CREATED.isoformat()
    assert row["operation_id"] == "op-1"


def test_a_record_that_is_not_there_is_none(tmp_path: Path) -> None:
    store = JsonLinesBackupStore(tmp_path / "c.jsonl")
    store.save(_record())

    assert store.load(LAB, "101", "other-backup") is None
    assert store.load(OTHER, "101", _record().backup_id) is None
    assert store.load_by_operation(OperationId("nope")) is None


def test_saving_the_same_key_replaces_the_record(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    store = JsonLinesBackupStore(path)
    store.save(_record(location="first"))

    store.save(_record(location="second"))

    assert [r["location"] for r in _rows(path)] == ["second"]


def test_records_of_one_server_are_listed_oldest_first(tmp_path: Path) -> None:
    store = JsonLinesBackupStore(tmp_path / "c.jsonl")
    newer = _record(backup_id="b", created_at=CREATED + timedelta(hours=1))
    older = _record(backup_id="a", operation_id="op-0")
    elsewhere = _record(backup_id="c", server_identifier="102")
    for record in (newer, elsewhere, older):
        store.save(record)

    assert store.load_for(LAB, "101") == (older, newer)
    assert store.load_for(OTHER, "101") == ()


def test_the_record_made_by_an_operation_is_found(tmp_path: Path) -> None:
    store = JsonLinesBackupStore(tmp_path / "c.jsonl")
    record = _record()
    store.save(record)
    store.save(_record(backup_id="x", operation_id="op-2"))

    assert store.load_by_operation(OperationId("op-1")) == record


def test_other_records_of_the_file_are_kept(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesOperationStore(path).save(_operation(PowerAction.BACKUP))
    JsonLinesBackupStore(path).save(_record())

    JsonLinesOperationStore(path).save(_operation(PowerAction.RESTORE))

    assert [r["record_type"] for r in _rows(path)] == [
        "operation",
        "operation",
        "backup",
    ]
    assert JsonLinesBackupStore(path).load_by_operation(OperationId("op-1")) is not None
    assert JsonLinesServerAccessStore(path).load(LAB, "101") is None


@pytest.mark.parametrize("action", [PowerAction.BACKUP, PowerAction.RESTORE])
def test_an_operation_line_accepts_backup_and_restore(
    tmp_path: Path, action: PowerAction
) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesOperationStore(path).save(_operation(action))

    loaded = JsonLinesOperationStore(path).load(OperationId("op-" + action.value))

    assert loaded.action is action
    assert _rows(path)[0]["action"] == action.value


def test_a_file_without_backup_lines_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesOperationStore(path).save(_operation(PowerAction.REMOVE))

    store = JsonLinesBackupStore(path)

    assert store.load_for(LAB, "101") == ()
    assert JsonLinesOperationStore(path).load(OperationId("op-remove")).action is (
        PowerAction.REMOVE
    )


def test_a_missing_file_has_no_records(tmp_path: Path) -> None:
    assert JsonLinesBackupStore(tmp_path / "none.jsonl").load_for(LAB, "101") == ()


def test_an_unknown_field_is_rejected_by_name_only(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesBackupStore(path).save(_record())
    row = {**_rows(path)[0], "token": "hunter2-secret"}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ConfigurationFormatError) as error:
        JsonLinesBackupStore(path).load_for(LAB, "101")

    assert "token" in str(error.value)
    assert "hunter2-secret" not in str(error.value)


@pytest.mark.parametrize("missing", sorted(RECORD_FIELDS - {"record_type"}))
def test_a_missing_field_is_rejected(tmp_path: Path, missing: str) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesBackupStore(path).save(_record())
    row = {k: v for k, v in _rows(path)[0].items() if k != missing}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ConfigurationFormatError, match=missing):
        JsonLinesBackupStore(path).load_for(LAB, "101")


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "tape"},
        {"created_at": "2026-10-20T10:00:00"},
        {"created_at": "yesterday"},
        {"location": None},
        {"location": ""},
    ],
)
def test_a_value_that_is_not_valid_is_rejected(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesBackupStore(path).save(_record())
    row = {**_rows(path)[0], **changes}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ConfigurationFormatError):
        JsonLinesBackupStore(path).load_for(LAB, "101")


def test_the_file_is_replaced_atomically_and_leaves_no_temporary_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    store = JsonLinesBackupStore(path)

    store.save(_record())
    store.save(_record(backup_id="b"))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.jsonl"]
    assert path.read_text("utf-8").endswith("\n")
    assert "\r" not in path.read_text("utf-8")
