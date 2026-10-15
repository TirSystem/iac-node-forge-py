import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from iac_node_forge.application.errors import ConfigurationFormatError
from iac_node_forge.application.ports import ConfigurationStore
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.infrastructure.configuration import (
    JsonLinesConfigurationStore,
    default_configuration_path,
)
from iac_node_forge.infrastructure.credentials import PlatformCredentialStore
from tests.unit.infrastructure.credentials.memory_keyring import MemoryKeyring

REFERENCE = CredentialReference("lab-proxmox", "keyring")


def _configuration(
    identifier: str = "lab",
    display_name: str = "Lab",
    reference: CredentialReference | None = REFERENCE,
) -> ProviderConfiguration:
    return ProviderConfiguration(
        configuration_id=ConfigurationId(identifier),
        provider_kind=ProviderKind.PROXMOX_VE,
        display_name=display_name,
        address="https://pve.example:8006",
        credential_reference=reference,
    )


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def test_store_satisfies_the_port(tmp_path: Path) -> None:
    assert isinstance(
        JsonLinesConfigurationStore(tmp_path / "c.jsonl"), ConfigurationStore
    )


def test_load_all_returns_nothing_when_file_is_missing(tmp_path: Path) -> None:
    store = JsonLinesConfigurationStore(tmp_path / "missing.jsonl")

    assert store.load_all() == ()
    assert store.managed_servers(ConfigurationId("lab")) == ()


def test_save_then_load_all_round_trips(tmp_path: Path) -> None:
    store = JsonLinesConfigurationStore(tmp_path / "sub" / "c.jsonl")
    plain = _configuration("plain", reference=None)

    store.save(_configuration())
    store.save(plain)

    assert store.load_all() == (_configuration(), plain)


def test_save_upserts_configuration_and_reference(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    store = JsonLinesConfigurationStore(path)
    store.save(_configuration())

    store.save(_configuration(display_name="Renamed"))

    assert [c.display_name for c in store.load_all()] == ["Renamed"]
    types = [row["record_type"] for row in _lines(path)]
    assert types.count("provider_configuration") == 1
    assert types.count("credential_reference") == 1


def test_written_file_is_utf8_lf_with_one_trailing_newline(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesConfigurationStore(path).save(_configuration(display_name="Lab æøå"))

    raw = path.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    assert "Lab æøå" in raw.decode("utf-8")


def test_file_holds_no_secret_after_credential_put(
    tmp_path: Path, memory_keyring: MemoryKeyring
) -> None:
    path = tmp_path / "c.jsonl"
    secret = "s3cr3t-token-value"
    JsonLinesConfigurationStore(path).save(_configuration())

    PlatformCredentialStore().put(REFERENCE, Credentials(secret))
    JsonLinesConfigurationStore(path).save(_configuration(display_name="Again"))

    assert secret not in path.read_text("utf-8")
    assert memory_keyring.get_password("iac-node-forge", "lab-proxmox") == secret
    allowed = {
        "record_type",
        "configuration_id",
        "provider_kind",
        "display_name",
        "address",
        "credential_reference_name",
        "name",
        "store_kind",
    }
    assert all(set(row) <= allowed for row in _lines(path))


def test_managed_servers_are_read_per_configuration(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    stamp = "2026-10-15T08:30:00+02:00"
    rows = [
        {"record_type": "managed_server", "configuration_id": "lab",
         "server_identifier": "101", "recorded_at": stamp},
        {"record_type": "managed_server", "configuration_id": "other",
         "server_identifier": "7", "recorded_at": stamp},
    ]  # fmt: skip
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    store = JsonLinesConfigurationStore(path)

    (record,) = store.managed_servers(ConfigurationId("lab"))

    assert record.server_identifier == "101"
    assert record.recorded_at.utcoffset() is not None
    assert record.recorded_at.astimezone(UTC) == datetime(
        2026, 10, 15, 6, 30, tzinfo=UTC
    )


def test_save_keeps_managed_server_records(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    row = {
        "record_type": "managed_server",
        "configuration_id": "lab",
        "server_identifier": "101",
        "recorded_at": "2026-10-15T08:30:00+00:00",
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    store = JsonLinesConfigurationStore(path)

    store.save(_configuration())

    assert len(store.managed_servers(ConfigurationId("lab"))) == 1


def test_unknown_record_type_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text('{"record_type":"secret","value":"x"}\n', encoding="utf-8")

    with pytest.raises(ConfigurationFormatError, match="unknown record type"):
        JsonLinesConfigurationStore(path).load_all()


def test_unknown_field_is_rejected_without_echoing_its_value(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    row = {
        "record_type": "credential_reference",
        "name": "lab",
        "store_kind": "keyring",
        "password": "hunter2",
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ConfigurationFormatError, match="password") as caught:
        JsonLinesConfigurationStore(path).load_all()
    assert "hunter2" not in str(caught.value)


@pytest.mark.parametrize(
    "content",
    [
        "not json\n",
        "[1]\n",
        '{"record_type":"credential_reference","name":"a"}\n',
        '{"record_type":"credential_reference","name":"a","store_kind":1}\n',
    ],
)
def test_malformed_lines_are_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigurationFormatError):
        JsonLinesConfigurationStore(path).load_all()


def test_dangling_credential_reference_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    row = {
        "record_type": "provider_configuration",
        "configuration_id": "lab",
        "provider_kind": "proxmox_ve",
        "display_name": "Lab",
        "address": "https://pve",
        "credential_reference_name": "nope",
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ConfigurationFormatError):
        JsonLinesConfigurationStore(path).load_all()


def test_failed_replace_leaves_old_file_and_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "c.jsonl"
    store = JsonLinesConfigurationStore(path)
    store.save(_configuration())
    before = path.read_bytes()

    def fail(src: object, dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        store.save(_configuration(display_name="Changed"))

    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["c.jsonl"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_file_mode_is_owner_only_on_posix(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    JsonLinesConfigurationStore(path).save(_configuration())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_default_path_uses_override() -> None:
    env = {"IAC_NODE_FORGE_CONFIG": "/custom/c.jsonl", "APPDATA": "/ignored"}

    assert default_configuration_path(env, "win32") == Path("/custom/c.jsonl")


def test_default_path_on_windows_uses_appdata() -> None:
    env = {"APPDATA": "C:\\Users\\me\\AppData\\Roaming"}

    expected = Path(env["APPDATA"]) / "iac-node-forge" / "configuration.jsonl"
    assert default_configuration_path(env, "win32") == expected


def test_default_path_on_linux_uses_xdg_config_home() -> None:
    env = {"XDG_CONFIG_HOME": "/xdg"}

    expected = Path("/xdg") / "iac-node-forge" / "configuration.jsonl"
    assert default_configuration_path(env, "linux") == expected


def test_default_path_on_linux_falls_back_to_home_config(tmp_path: Path) -> None:
    result = default_configuration_path({}, "linux", home=tmp_path)

    assert result == tmp_path / ".config" / "iac-node-forge" / "configuration.jsonl"
