"""Desired configurations, intents and managed servers in the JSON Lines file."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from iac_node_forge.application.errors import (
    ConfigurationFormatError,
    ProvisionIntentNotFoundError,
)
from iac_node_forge.application.ports import (
    ConfigurationStore,
    DesiredConfigurationStore,
    OperationStore,
    ProvisionIntentStore,
)
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
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
from iac_node_forge.domain.provider_value import ProviderValue
from iac_node_forge.domain.provision_intent import (
    IntentId,
    IntentState,
    ProvisionIntent,
)
from iac_node_forge.infrastructure.configuration import (
    JsonLinesConfigurationStore,
    JsonLinesDesiredConfigurationStore,
    JsonLinesOperationStore,
    JsonLinesProvisionIntentStore,
)

LAB = ConfigurationId("lab")
EDGE = ConfigurationId("edge")
WHEN = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)


def _desired(
    name: str = "small", configuration: ConfigurationId = LAB
) -> DesiredConfiguration:
    return DesiredConfiguration(
        configuration,
        name,
        "pve1/local:vztmpl/alpine.tar.xz",
        ("deploy", "backup"),
        "install nginx",
        (ProviderValue("node", "pve1"), ProviderValue("cores", "2")),
    )


def _intent(
    intent_id: str = "i-1",
    server_name: str = "web-1",
    state: IntentState = IntentState.PENDING,
    operation_id: str | None = None,
    configuration: ConfigurationId = LAB,
) -> ProvisionIntent:
    return ProvisionIntent(
        IntentId(intent_id),
        configuration,
        "small",
        server_name,
        "pve1/local:vztmpl/alpine.tar.xz",
        (ProviderValue("node", "pve1"), ProviderValue("cores", "2")),
        state,
        WHEN,
        reserved_identifier="102",
        operation_id=OperationId(operation_id) if operation_id else None,
    )


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def _of_type(path: Path, record_type: str) -> list[dict[str, object]]:
    return [r for r in _rows(path) if r["record_type"] == record_type]


def test_the_stores_satisfy_their_ports(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"

    assert isinstance(
        JsonLinesDesiredConfigurationStore(path), DesiredConfigurationStore
    )
    assert isinstance(JsonLinesProvisionIntentStore(path), ProvisionIntentStore)
    assert isinstance(JsonLinesConfigurationStore(path), ConfigurationStore)


def test_a_desired_configuration_round_trips_with_its_children(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "c.jsonl"
    store = JsonLinesDesiredConfigurationStore(path)

    store.save(_desired())

    assert store.load(LAB, "small") == _desired()
    assert JsonLinesDesiredConfigurationStore(path).load(LAB, "small") == _desired()


def test_one_line_per_entity_and_one_per_child(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"

    JsonLinesDesiredConfigurationStore(path).save(_desired())

    assert _rows(path) == [
        {
            "record_type": "desired_configuration",
            "configuration_id": "lab",
            "desired_name": "small",
            "image": "pve1/local:vztmpl/alpine.tar.xz",
            "bootstrap_hint": "install nginx",
        },
        {
            "record_type": "access_key_name",
            "configuration_id": "lab",
            "desired_name": "small",
            "key_name": "deploy",
        },
        {
            "record_type": "access_key_name",
            "configuration_id": "lab",
            "desired_name": "small",
            "key_name": "backup",
        },
        {
            "record_type": "desired_configuration_value",
            "configuration_id": "lab",
            "desired_name": "small",
            "value_name": "node",
            "value": "pve1",
        },
        {
            "record_type": "desired_configuration_value",
            "configuration_id": "lab",
            "desired_name": "small",
            "value_name": "cores",
            "value": "2",
        },
    ]


def test_a_description_without_hint_keys_or_values_round_trips(tmp_path: Path) -> None:
    store = JsonLinesDesiredConfigurationStore(tmp_path / "c.jsonl")
    bare = DesiredConfiguration(LAB, "bare", "img")

    store.save(bare)

    assert store.load(LAB, "bare") == bare
    assert _rows(tmp_path / "c.jsonl")[0]["bootstrap_hint"] is None


def test_saving_the_same_key_replaces_the_description_and_its_children(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    store = JsonLinesDesiredConfigurationStore(path)
    store.save(_desired())
    replacement = DesiredConfiguration(
        LAB, "small", "other", ("one",), None, (ProviderValue("node", "pve2"),)
    )

    store.save(replacement)

    assert store.load(LAB, "small") == replacement
    assert len(_of_type(path, "desired_configuration")) == 1
    assert len(_of_type(path, "access_key_name")) == 1
    assert len(_of_type(path, "desired_configuration_value")) == 1


def test_a_missing_description_is_none_and_load_all_is_per_configuration(
    tmp_path: Path,
) -> None:
    store = JsonLinesDesiredConfigurationStore(tmp_path / "c.jsonl")
    assert store.load(LAB, "small") is None
    assert store.load_all(LAB) == ()
    store.save(_desired("a"))
    store.save(_desired("b"))
    store.save(_desired("a", EDGE))

    assert [d.desired_name for d in store.load_all(LAB)] == ["a", "b"]
    assert [d.desired_name for d in store.load_all(EDGE)] == ["a"]
    assert store.load(EDGE, "b") is None


def test_the_file_holds_the_names_of_access_keys_and_no_key_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesDesiredConfigurationStore(path).save(_desired())

    assert {r["key_name"] for r in _of_type(path, "access_key_name")} == {
        "deploy",
        "backup",
    }
    assert "ssh-" not in path.read_text("utf-8")


def test_an_intent_round_trips_with_its_values(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    store = JsonLinesProvisionIntentStore(path)
    intent = _intent(operation_id="op-1")

    store.save(intent)

    assert store.load(IntentId("i-1")) == intent
    [row] = _of_type(path, "provision_intent")
    assert row["state"] == "pending"
    assert row["reserved_identifier"] == "102"
    assert row["operation_id"] == "op-1"
    assert row["recorded_at"] == "2026-10-18T09:30:00+00:00"
    assert [r["value_name"] for r in _of_type(path, "provision_intent_value")] == [
        "node",
        "cores",
    ]


def test_an_intent_before_the_request_has_no_operation_or_identifier(
    tmp_path: Path,
) -> None:
    store = JsonLinesProvisionIntentStore(tmp_path / "c.jsonl")
    bare = ProvisionIntent(
        IntentId("i-2"), LAB, "small", "web", "img", (), IntentState.PENDING, WHEN
    )

    store.save(bare)

    assert store.load(IntentId("i-2")) == bare


def test_an_unknown_intent_raises_not_found(tmp_path: Path) -> None:
    with pytest.raises(ProvisionIntentNotFoundError):
        JsonLinesProvisionIntentStore(tmp_path / "c.jsonl").load(IntentId("i-9"))


def test_saving_an_intent_again_replaces_it(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    store = JsonLinesProvisionIntentStore(path)
    store.save(_intent())

    store.save(_intent(state=IntentState.ENDED, operation_id="op-1"))

    assert store.load(IntentId("i-1")).state is IntentState.ENDED
    assert len(_of_type(path, "provision_intent")) == 1
    assert len(_of_type(path, "provision_intent_value")) == 2


def test_only_a_pending_intent_of_that_name_and_configuration_is_pending(
    tmp_path: Path,
) -> None:
    store = JsonLinesProvisionIntentStore(tmp_path / "c.jsonl")
    store.save(_intent("i-1", "web-1"))
    store.save(_intent("i-2", "web-2", IntentState.ENDED))
    store.save(_intent("i-3", "web-3", configuration=EDGE))

    pending = store.find_pending(LAB, "web-1")

    assert pending is not None
    assert pending.intent_id == "i-1"
    assert store.find_pending(LAB, "web-2") is None
    assert store.find_pending(LAB, "web-3") is None
    assert store.find_pending(EDGE, "web-3") is not None
    assert store.find_pending(LAB, "nope") is None


def test_an_intent_is_found_by_its_operation(tmp_path: Path) -> None:
    store = JsonLinesProvisionIntentStore(tmp_path / "c.jsonl")
    store.save(_intent("i-1", operation_id="op-1"))
    store.save(_intent("i-2", "web-2"))

    found = store.load_by_operation(OperationId("op-1"))

    assert found is not None
    assert found.intent_id == "i-1"
    assert store.load_by_operation(OperationId("op-9")) is None


def test_a_managed_server_is_saved_once_and_kept_with_the_other_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    configurations = JsonLinesConfigurationStore(path)
    record = ManagedServerRecord(LAB, "102", WHEN)
    configurations.save(
        ProviderConfiguration(LAB, ProviderKind.PROXMOX_VE, "Lab", "https://pve:8006")
    )

    configurations.save_managed_server(record)
    configurations.save_managed_server(ManagedServerRecord(LAB, "102", WHEN))
    configurations.save_managed_server(ManagedServerRecord(LAB, "103", WHEN))

    assert [r.server_identifier for r in configurations.managed_servers(LAB)] == [
        "102",
        "103",
    ]
    assert len(configurations.load_all()) == 1


def test_all_four_stores_keep_each_others_records(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    configurations = JsonLinesConfigurationStore(path)
    operations = JsonLinesOperationStore(path)
    descriptions = JsonLinesDesiredConfigurationStore(path)
    intents = JsonLinesProvisionIntentStore(path)
    operation = Operation(
        OperationId("op-1"),
        LAB,
        None,
        PowerAction.PROVISION,
        OperationState.RUNNING,
        WHEN,
    )

    configurations.save(
        ProviderConfiguration(LAB, ProviderKind.PROXMOX_VE, "Lab", "https://pve:8006")
    )
    descriptions.save(_desired())
    intents.save(_intent(operation_id="op-1"))
    operations.save(operation)
    configurations.save_managed_server(ManagedServerRecord(LAB, "102", WHEN))
    descriptions.save(_desired("second"))

    assert isinstance(operations, OperationStore)
    assert [c.display_name for c in configurations.load_all()] == ["Lab"]
    assert operations.load(OperationId("op-1")) == operation
    assert intents.load(IntentId("i-1")) == _intent(operation_id="op-1")
    assert [d.desired_name for d in descriptions.load_all(LAB)] == ["small", "second"]
    assert len(configurations.managed_servers(LAB)) == 1


def test_no_temporary_file_is_left_behind_and_the_file_uses_line_feeds(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesDesiredConfigurationStore(path).save(_desired())
    JsonLinesProvisionIntentStore(path).save(_intent())

    assert [p.name for p in tmp_path.iterdir()] == ["c.jsonl"]
    data = path.read_bytes()
    assert b"\r" not in data
    assert data.endswith(b"\n")


def _write(path: Path, *rows: dict[str, object]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")


def _desired_row(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "record_type": "desired_configuration",
        "configuration_id": "lab",
        "desired_name": "small",
        "image": "img",
        "bootstrap_hint": None,
    }
    row.update(changes)
    return row


def test_a_key_line_without_its_description_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    _write(
        path,
        {
            "record_type": "access_key_name",
            "configuration_id": "lab",
            "desired_name": "small",
            "key_name": "deploy",
        },
    )

    with pytest.raises(ConfigurationFormatError):
        JsonLinesDesiredConfigurationStore(path).load(LAB, "small")


def test_a_value_line_without_its_intent_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    _write(
        path,
        {
            "record_type": "provision_intent_value",
            "intent_id": "i-9",
            "value_name": "node",
            "value": "pve1",
        },
    )

    with pytest.raises(ConfigurationFormatError):
        JsonLinesProvisionIntentStore(path).load(IntentId("i-9"))


def test_an_unknown_field_of_a_description_is_rejected_without_its_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"
    _write(path, _desired_row(private_key="s3cret-value"))

    with pytest.raises(ConfigurationFormatError) as raised:
        JsonLinesDesiredConfigurationStore(path).load(LAB, "small")

    assert "private_key" in str(raised.value)
    assert "s3cret-value" not in str(raised.value)


@pytest.mark.parametrize(
    "changes",
    [{"bootstrap_hint": "x" * 501}, {"image": "  "}, {"image": None}],
)
def test_an_invalid_description_record_is_rejected(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    path = tmp_path / "c.jsonl"
    _write(path, _desired_row(**changes))

    with pytest.raises(ConfigurationFormatError):
        JsonLinesDesiredConfigurationStore(path).load(LAB, "small")


def test_an_intent_with_an_unknown_state_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesProvisionIntentStore(path).save(_intent())
    lines = _rows(path)
    lines[0]["state"] = "half-done"
    _write(path, *lines)

    with pytest.raises(ConfigurationFormatError):
        JsonLinesProvisionIntentStore(path).load(IntentId("i-1"))


def test_an_unknown_record_type_is_still_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    _write(path, {"record_type": "private_key", "name": "deploy"})

    with pytest.raises(ConfigurationFormatError):
        JsonLinesDesiredConfigurationStore(path).load_all(LAB)
