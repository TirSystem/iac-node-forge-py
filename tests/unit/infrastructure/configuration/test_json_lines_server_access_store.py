"""ServerAccess records in the JSON Lines file (ERD-001, ADR-0009)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from iac_node_forge.application.errors import ConfigurationFormatError
from iac_node_forge.application.ports import ServerAccessStore
from iac_node_forge.domain.operation import Operation, OperationId, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.server_access import ServerAccess
from iac_node_forge.infrastructure.configuration import (
    JsonLinesConfigurationStore,
    JsonLinesOperationStore,
    JsonLinesServerAccessStore,
)

LAB = ConfigurationId("lab")
EDGE = ConfigurationId("edge")


def access(
    server: str = "100",
    host: str | None = "web.example.org",
    port: int = 2222,
    configuration: ConfigurationId = LAB,
    hint: str | None = "~/.ssh/id_ed25519",
) -> ServerAccess:
    return ServerAccess(configuration, server, host, port, "deploy", hint)


def rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def write_lines(path: Path, *lines: dict[str, object]) -> None:
    path.write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )


def test_the_store_satisfies_its_port(tmp_path: Path) -> None:
    assert isinstance(
        JsonLinesServerAccessStore(tmp_path / "c.jsonl"), ServerAccessStore
    )


def test_an_access_round_trips_with_its_nulls(tmp_path: Path) -> None:
    store = JsonLinesServerAccessStore(tmp_path / "sub" / "c.jsonl")
    bare = access(server="101", host=None, port=22, hint=None)

    store.save(access())
    store.save(bare)

    assert store.load(LAB, "100") == access()
    assert store.load(LAB, "101") == bare
    assert (
        JsonLinesServerAccessStore(tmp_path / "sub" / "c.jsonl").load(LAB, "101")
        == bare
    )


def test_nothing_is_found_for_an_unknown_server_or_a_missing_file(
    tmp_path: Path,
) -> None:
    store = JsonLinesServerAccessStore(tmp_path / "c.jsonl")
    assert store.load(LAB, "100") is None

    store.save(access())

    assert store.load(LAB, "999") is None
    assert store.load(EDGE, "100") is None


def test_the_key_is_the_configuration_and_the_server(tmp_path: Path) -> None:
    store = JsonLinesServerAccessStore(tmp_path / "c.jsonl")

    store.save(access(configuration=LAB))
    store.save(access(configuration=EDGE, host="edge.example.org"))

    assert store.load(LAB, "100") == access(configuration=LAB)
    loaded = store.load(EDGE, "100")
    assert loaded is not None and loaded.host == "edge.example.org"


def test_saving_again_replaces_the_access_of_the_same_server(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    store = JsonLinesServerAccessStore(path)

    store.save(access(port=22))
    store.save(access(port=2200))

    loaded = store.load(LAB, "100")
    assert loaded is not None and loaded.port == 2200
    assert len(rows(path)) == 1


def test_the_line_has_the_fields_of_the_erd_and_the_port_is_a_number(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.jsonl"

    JsonLinesServerAccessStore(path).save(access())

    assert rows(path) == [
        {
            "record_type": "server_access",
            "configuration_id": "lab",
            "server_identifier": "100",
            "host": "web.example.org",
            "port": 2222,
            "user": "deploy",
            "identity_hint": "~/.ssh/id_ed25519",
        }
    ]


def test_no_field_can_hold_a_secret(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesServerAccessStore(path).save(access())

    names = set(rows(path)[0])

    assert not {n for n in names if any(w in n for w in ("pass", "secret", "token"))}
    assert "private_key" not in names


def test_the_file_is_written_atomically_and_without_leftovers(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"

    JsonLinesServerAccessStore(path).save(access())

    assert [p.name for p in tmp_path.iterdir()] == ["c.jsonl"]
    assert b"\r" not in path.read_bytes()


def test_other_records_are_kept_when_an_access_is_saved(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesConfigurationStore(path).save(
        ProviderConfiguration(LAB, ProviderKind.PROXMOX_VE, "Lab", "pve:8006")
    )
    operation = Operation(
        OperationId("op-1"),
        LAB,
        "100",
        PowerAction.START,
        OperationState.RUNNING,
        datetime(2026, 10, 18, tzinfo=UTC),
    )
    JsonLinesOperationStore(path).save(operation)

    JsonLinesServerAccessStore(path).save(access())

    assert JsonLinesOperationStore(path).load(OperationId("op-1")) == operation
    assert len(JsonLinesConfigurationStore(path).load_all()) == 1
    assert {r["record_type"] for r in rows(path)} == {
        "provider_configuration",
        "operation",
        "server_access",
    }


def test_a_file_from_before_the_access_record_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    write_lines(
        path,
        {
            "record_type": "provider_configuration",
            "configuration_id": "lab",
            "provider_kind": "proxmox_ve",
            "display_name": "Lab",
            "address": "pve:8006",
            "credential_reference_name": None,
        },
    )

    store = JsonLinesServerAccessStore(path)

    assert store.load(LAB, "100") is None
    store.save(access())
    assert store.load(LAB, "100") == access()
    assert len(JsonLinesConfigurationStore(path).load_all()) == 1


def good_line(**overrides: object) -> dict[str, object]:
    line: dict[str, object] = {
        "record_type": "server_access",
        "configuration_id": "lab",
        "server_identifier": "100",
        "host": None,
        "port": 22,
        "user": "deploy",
        "identity_hint": None,
    }
    line.update(overrides)
    return line


def test_a_line_with_valid_fields_loads(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    write_lines(path, good_line())

    assert JsonLinesServerAccessStore(path).load(LAB, "100") == ServerAccess(
        LAB, "100", None, 22, "deploy", None
    )


def test_an_unknown_field_is_rejected_by_name_only(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    write_lines(path, good_line(password="hunter2"))

    with pytest.raises(ConfigurationFormatError) as caught:
        JsonLinesServerAccessStore(path).load(LAB, "100")

    assert "password" in str(caught.value)
    assert "hunter2" not in str(caught.value)


def test_a_missing_field_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    line = good_line()
    del line["user"]
    write_lines(path, line)

    with pytest.raises(ConfigurationFormatError):
        JsonLinesServerAccessStore(path).load(LAB, "100")


@pytest.mark.parametrize(
    "overrides",
    [
        {"port": "22"},
        {"port": True},
        {"port": 22.5},
        {"port": 0},
        {"port": 70000},
        {"user": None},
        {"user": ""},
        {"user": "-oProxyCommand=x"},
        {"host": "-bad"},
        {"host": "a b"},
        {"identity_hint": "x" * 501},
        {"configuration_id": None},
        {"server_identifier": 100},
    ],
)
def test_a_line_that_breaks_a_rule_is_rejected(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    path = tmp_path / "c.jsonl"
    write_lines(path, good_line(**overrides))

    with pytest.raises(ConfigurationFormatError):
        JsonLinesServerAccessStore(path).load(LAB, "100")
