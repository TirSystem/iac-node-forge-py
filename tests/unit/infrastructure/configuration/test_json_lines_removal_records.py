"""The removal records in the client's JSON Lines file (ERD-001, revision 5).

An operation line carries the nullable ``consequence``; a managed-server line
carries ``ended_at`` and ``ended_by_operation_id`` and is ended, never deleted.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from iac_node_forge.application.errors import ConfigurationFormatError
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.operation import Operation, OperationId, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.infrastructure.configuration import (
    JsonLinesConfigurationStore,
    JsonLinesOperationStore,
)

LAB = ConfigurationId("lab")
OTHER = ConfigurationId("other")
RECORDED = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
ENDED = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)
OP = OperationId("op-remove")
CONSEQUENCE = "This destroys server 101 now: the machine and its disks are deleted."
MANAGED_FIELDS = {
    "record_type",
    "configuration_id",
    "server_identifier",
    "recorded_at",
    "ended_at",
    "ended_by_operation_id",
}


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def _managed_rows(path: Path) -> list[dict[str, object]]:
    return [r for r in _rows(path) if r["record_type"] == "managed_server"]


def _managed_row(**changes: object) -> dict[str, object]:
    return {
        "record_type": "managed_server",
        "configuration_id": "lab",
        "server_identifier": "100",
        "recorded_at": RECORDED.isoformat(),
        "ended_at": None,
        "ended_by_operation_id": None,
        **changes,
    }


def _removal(consequence: str | None = CONSEQUENCE) -> Operation:
    return Operation(
        OP,
        LAB,
        "101",
        PowerAction.REMOVE,
        OperationState.SUCCEEDED,
        RECORDED,
        RECORDED + timedelta(seconds=5),
        consequence=consequence,
    )


def test_an_operation_keeps_its_consequence(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesOperationStore(path).save(_removal())

    loaded = JsonLinesOperationStore(path).load(OP)

    assert loaded.consequence == CONSEQUENCE
    assert _rows(path)[0]["consequence"] == CONSEQUENCE


def test_an_operation_without_a_consequence_is_written_with_a_null(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesOperationStore(path).save(_removal(None))

    assert _rows(path)[0]["consequence"] is None
    assert JsonLinesOperationStore(path).load(OP).consequence is None


def test_a_file_written_before_the_removal_fields_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    operation = {
        "record_type": "operation",
        "operation_id": "op-old",
        "configuration_id": "lab",
        "server_identifier": "100",
        "action": "stop",
        "state": "running",
        "started_at": RECORDED.isoformat(),
        "finished_at": None,
        "provider_reference": None,
        "detail": None,
    }
    managed = _managed_row()
    del managed["ended_at"], managed["ended_by_operation_id"]
    path.write_text(
        "\n".join(json.dumps(row) for row in (operation, managed)) + "\n", "utf-8"
    )

    loaded = JsonLinesOperationStore(path).load(OperationId("op-old"))
    (record,) = JsonLinesConfigurationStore(path).managed_servers(LAB)

    assert loaded.consequence is None
    assert (record.ended_at, record.ended_by_operation_id) == (None, None)
    JsonLinesOperationStore(path).save(_removal())
    assert [set(row) for row in _managed_rows(path)] == [MANAGED_FIELDS]


def test_an_unknown_field_is_still_rejected_without_echoing_its_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text(json.dumps(_managed_row(token="s3cret-value")) + "\n", "utf-8")

    with pytest.raises(ConfigurationFormatError) as raised:
        JsonLinesConfigurationStore(path).managed_servers(LAB)

    assert "token" in str(raised.value)
    assert "s3cret-value" not in str(raised.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"ended_at": ENDED.isoformat()},
        {"ended_by_operation_id": "op-remove"},
        {"ended_at": "2026-10-18T09:30:00", "ended_by_operation_id": "op"},
        {"ended_at": "yesterday", "ended_by_operation_id": "op"},
        {"ended_at": 5, "ended_by_operation_id": "op"},
    ],
)
def test_an_inconsistent_ended_record_is_rejected(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text(json.dumps(_managed_row(**changes)) + "\n", "utf-8")

    with pytest.raises(ConfigurationFormatError):
        JsonLinesConfigurationStore(path).managed_servers(LAB)


def test_ending_a_managed_server_keeps_the_record_and_hides_it(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    store = JsonLinesConfigurationStore(path)
    store.save_managed_server(ManagedServerRecord(LAB, "101", RECORDED))
    store.save_managed_server(ManagedServerRecord(LAB, "102", RECORDED))

    store.end_managed_server(LAB, "101", ENDED, OP)

    assert [r.server_identifier for r in store.managed_servers(LAB)] == ["102"]
    rows = _managed_rows(path)
    assert len(rows) == 2
    ended = next(r for r in rows if r["server_identifier"] == "101")
    assert ended["ended_at"] == ENDED.isoformat()
    assert ended["ended_by_operation_id"] == "op-remove"
    assert ended["recorded_at"] == RECORDED.isoformat()


def test_ending_twice_or_ending_an_unmanaged_server_changes_nothing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    store = JsonLinesConfigurationStore(path)
    store.save_managed_server(ManagedServerRecord(LAB, "101", RECORDED))
    store.end_managed_server(LAB, "101", ENDED, OP)
    before = path.read_text("utf-8")

    store.end_managed_server(LAB, "101", ENDED + timedelta(days=1), OperationId("op-2"))
    store.end_managed_server(LAB, "999", ENDED, OP)
    store.end_managed_server(OTHER, "101", ENDED, OP)

    assert path.read_text("utf-8") == before


def test_ending_in_a_missing_file_creates_nothing(tmp_path: Path) -> None:
    path = tmp_path / "missing.jsonl"

    JsonLinesConfigurationStore(path).end_managed_server(LAB, "101", ENDED, OP)

    assert not path.exists()


def test_a_server_of_the_same_identifier_created_later_is_a_new_record(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    store = JsonLinesConfigurationStore(path)
    store.save_managed_server(ManagedServerRecord(LAB, "101", RECORDED))
    store.end_managed_server(LAB, "101", ENDED, OP)
    later = ManagedServerRecord(LAB, "101", ENDED + timedelta(hours=1))

    store.save_managed_server(later)
    store.save_managed_server(later)

    assert store.managed_servers(LAB) == (later,)
    assert len(_managed_rows(path)) == 2


def test_ending_the_new_record_leaves_the_old_one_as_it_was(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    store = JsonLinesConfigurationStore(path)
    store.save_managed_server(ManagedServerRecord(LAB, "101", RECORDED))
    store.end_managed_server(LAB, "101", ENDED, OP)
    store.save_managed_server(ManagedServerRecord(LAB, "101", ENDED))

    store.end_managed_server(LAB, "101", ENDED + timedelta(days=1), OperationId("op-2"))

    assert store.managed_servers(LAB) == ()
    assert [r["ended_by_operation_id"] for r in _managed_rows(path)] == [
        "op-remove",
        "op-2",
    ]


def test_the_operation_and_configuration_stores_keep_each_others_removal_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    configurations = JsonLinesConfigurationStore(path)
    operations = JsonLinesOperationStore(path)
    configurations.save_managed_server(ManagedServerRecord(LAB, "101", RECORDED))
    operations.save(_removal())

    configurations.end_managed_server(LAB, "101", ENDED, OP)
    operations.save(_removal("another text"))

    assert operations.load(OP).consequence == "another text"
    assert configurations.managed_servers(LAB) == ()
    assert _managed_rows(path)[0]["ended_by_operation_id"] == "op-remove"


def test_the_removal_records_hold_no_secret_field(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    store = JsonLinesConfigurationStore(path)
    store.save_managed_server(ManagedServerRecord(LAB, "101", RECORDED))
    store.end_managed_server(LAB, "101", ENDED, OP)
    JsonLinesOperationStore(path).save(_removal())

    for row in _rows(path):
        assert not {"token", "secret", "password", "key"} & set(row)
