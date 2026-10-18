"""The operation record in the client's JSON Lines file (ERD-001)."""

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from iac_node_forge.application.errors import (
    ConfigurationFormatError,
    OperationNotFoundError,
)
from iac_node_forge.application.ports import OperationStore
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.operation import (
    Operation,
    OperationId,
    OperationState,
)
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.infrastructure.configuration import (
    JsonLinesConfigurationStore,
    JsonLinesOperationStore,
)

STARTED = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
FIELDS = {
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
    "consequence",
}


def _operation(
    operation_id: str = "op-1", state: OperationState = OperationState.RUNNING
) -> Operation:
    running = Operation(
        OperationId(operation_id),
        ConfigurationId("lab"),
        "100",
        PowerAction.RESTART,
        OperationState.RUNNING,
        STARTED,
        provider_reference="UPID:pve1:0001:qmreboot:100:root@pam!forge:",
    )
    if state is OperationState.RUNNING:
        return running
    return running.ended(state, STARTED + timedelta(seconds=7), "exit status OK")


def _configuration() -> ProviderConfiguration:
    return ProviderConfiguration(
        ConfigurationId("lab"),
        ProviderKind.PROXMOX_VE,
        "Lab",
        "https://pve.example:8006",
    )


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def test_store_satisfies_the_port(tmp_path: Path) -> None:
    assert isinstance(JsonLinesOperationStore(tmp_path / "c.jsonl"), OperationStore)


@pytest.mark.parametrize(
    "state", [OperationState.RUNNING, OperationState.SUCCEEDED, OperationState.FAILED]
)
def test_save_then_load_round_trips_an_operation(
    tmp_path: Path, state: OperationState
) -> None:
    store = JsonLinesOperationStore(tmp_path / "sub" / "c.jsonl")
    operation = _operation(state=state)

    store.save(operation)

    assert store.load(OperationId("op-1")) == operation


def test_the_record_is_a_new_process_away(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesOperationStore(path).save(_operation())

    assert JsonLinesOperationStore(path).load(OperationId("op-1")) == _operation()


def test_save_replaces_the_operation_with_the_same_identifier(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    store = JsonLinesOperationStore(path)
    store.save(_operation())
    store.save(_operation(state=OperationState.SUCCEEDED))
    store.save(_operation("op-2"))

    rows = _rows(path)

    assert [r["operation_id"] for r in rows] == ["op-1", "op-2"]
    assert store.load(OperationId("op-1")).state is OperationState.SUCCEEDED


def test_load_raises_when_no_operation_has_the_identifier(tmp_path: Path) -> None:
    store = JsonLinesOperationStore(tmp_path / "c.jsonl")
    store.save(_operation())

    with pytest.raises(OperationNotFoundError):
        store.load(OperationId("missing"))


def test_load_raises_when_the_file_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(OperationNotFoundError):
        JsonLinesOperationStore(tmp_path / "none.jsonl").load(OperationId("op-1"))


def test_the_record_has_exactly_the_fields_of_the_erd(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesOperationStore(path).save(_operation(state=OperationState.FAILED))

    row = _rows(path)[0]

    assert set(row) == FIELDS
    assert row["record_type"] == "operation"
    assert row["action"] == "restart"
    assert row["state"] == "failed"
    assert row["started_at"] == "2026-10-15T12:00:00+00:00"
    assert row["finished_at"] == "2026-10-15T12:00:07+00:00"


def test_operation_and_configuration_stores_keep_each_others_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    configurations = JsonLinesConfigurationStore(path)
    operations = JsonLinesOperationStore(path)
    configurations.save(_configuration())
    operations.save(_operation())
    configurations.save(
        ProviderConfiguration(
            ConfigurationId("edge"),
            ProviderKind.CONTABO,
            "Edge",
            "https://api.contabo.com",
        )
    )
    operations.save(_operation("op-2"))

    assert {c.configuration_id for c in configurations.load_all()} == {"lab", "edge"}
    assert operations.load(OperationId("op-1")) == _operation()
    assert operations.load(OperationId("op-2")) == _operation("op-2")


def test_a_file_written_before_operation_records_exist_is_read(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    lines = [
        {
            "record_type": "provider_configuration",
            "configuration_id": "lab",
            "provider_kind": "proxmox_ve",
            "display_name": "Lab",
            "address": "https://pve.example:8006",
            "credential_reference_name": None,
        },
        {
            "record_type": "managed_server",
            "configuration_id": "lab",
            "server_identifier": "100",
            "recorded_at": "2026-01-01T00:00:00+00:00",
        },
    ]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), "utf-8")
    store = JsonLinesOperationStore(path)

    store.save(_operation())

    kept = JsonLinesConfigurationStore(path)
    assert [c.display_name for c in kept.load_all()] == ["Lab"]
    assert kept.managed_servers(ConfigurationId("lab")) == (
        ManagedServerRecord(
            ConfigurationId("lab"), "100", datetime(2026, 1, 1, tzinfo=UTC)
        ),
    )
    assert store.load(OperationId("op-1")) == _operation()


def test_the_configuration_store_reads_a_file_with_operation_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesOperationStore(path).save(_operation())

    assert JsonLinesConfigurationStore(path).load_all() == ()


def _write_row(path: Path, **changes: object) -> None:
    row: dict[str, object] = {
        "record_type": "operation",
        "operation_id": "op-1",
        "configuration_id": "lab",
        "server_identifier": "100",
        "action": "stop",
        "state": "running",
        "started_at": "2026-10-15T12:00:00+00:00",
        "finished_at": None,
        "provider_reference": None,
        "detail": None,
    }
    row.update(changes)
    path.write_text(json.dumps(row) + "\n", "utf-8")


def test_an_unknown_field_is_rejected_without_echoing_its_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    _write_row(path, token="s3cret-value")

    with pytest.raises(ConfigurationFormatError) as raised:
        JsonLinesOperationStore(path).load(OperationId("op-1"))

    assert "token" in str(raised.value)
    assert "s3cret-value" not in str(raised.value)


def test_a_missing_field_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    _write_row(path)
    row = json.loads(path.read_text("utf-8"))
    del row["detail"]
    path.write_text(json.dumps(row) + "\n", "utf-8")

    with pytest.raises(ConfigurationFormatError):
        JsonLinesOperationStore(path).load(OperationId("op-1"))


@pytest.mark.parametrize(
    "changes",
    [
        {"action": "delete"},
        {"state": "done"},
        {"started_at": "2026-10-15T12:00:00"},
        {"started_at": "yesterday"},
        {"state": "succeeded"},
        {"detail": "x" * 501},
        {"detail": "  "},
        {"operation_id": 7},
        {"server_identifier": "  "},
    ],
)
def test_an_invalid_record_is_rejected(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    path = tmp_path / "c.jsonl"
    _write_row(path, **changes)

    with pytest.raises(ConfigurationFormatError):
        JsonLinesOperationStore(path).load(OperationId("op-1"))


def test_the_file_uses_line_feeds_only(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesOperationStore(path).save(_operation(state=OperationState.SUCCEEDED))

    data = path.read_bytes()

    assert b"\r" not in data
    assert data.endswith(b"\n")


@pytest.mark.skipif(os.name != "posix", reason="file modes are a POSIX concept")
def test_the_file_is_readable_by_its_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesOperationStore(path).save(_operation())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    JsonLinesOperationStore(tmp_path / "c.jsonl").save(_operation())

    assert [p.name for p in tmp_path.iterdir()] == ["c.jsonl"]


def test_a_provision_operation_without_a_server_identifier_round_trips(
    tmp_path: Path,
) -> None:
    store = JsonLinesOperationStore(tmp_path / "c.jsonl")
    operation = Operation(
        OperationId("op-p"),
        ConfigurationId("lab"),
        None,
        PowerAction.PROVISION,
        OperationState.RUNNING,
        STARTED,
    )

    store.save(operation)

    assert store.load(OperationId("op-p")) == operation
