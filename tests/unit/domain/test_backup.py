"""The backup value object, its kind, its record and the restore effect (ADR-0010)."""

import hashlib
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime

import pytest

from iac_node_forge.domain.backup import ALIAS_LENGTH, Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.backup_record import BackupRecord
from iac_node_forge.domain.errors import (
    EmptyValueError,
    InvalidBackupError,
    NaiveTimestampError,
    OperationTextTooLongError,
)
from iac_node_forge.domain.operation import MAX_CONSEQUENCE_LENGTH, OperationId
from iac_node_forge.domain.power_action import POWER_ACTIONS, PowerAction
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.restore_effect import RestoreEffect

WHEN = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
VOLID = "local:backup/vzdump-lxc-105-2026_10_25-12_00_00.tar"


def make(**changes: object) -> Backup:
    values: dict[str, object] = {
        "backup_id": VOLID,
        "kind": BackupKind.ARCHIVE,
        "location": f"storage local, volume {VOLID}",
        "created_at": WHEN,
    }
    return Backup(**{**values, **changes})  # type: ignore[arg-type]


def test_the_two_kinds_are_archive_and_snapshot() -> None:
    assert [kind.value for kind in BackupKind] == ["archive", "snapshot"]


def test_backup_and_restore_are_actions_of_an_operation_but_not_power_actions() -> None:
    assert PowerAction.BACKUP.value == "backup"
    assert PowerAction.RESTORE.value == "restore"
    assert PowerAction.BACKUP not in POWER_ACTIONS
    assert PowerAction.RESTORE not in POWER_ACTIONS
    assert len(POWER_ACTIONS) == 4


def test_a_backup_holds_what_the_provider_reports() -> None:
    backup = make(
        size_bytes=1024,
        label="nightly",
        guest_type="lxc",
        made_by_client=True,
        auto_delete_at=WHEN,
    )

    assert backup.backup_id == VOLID
    assert backup.kind is BackupKind.ARCHIVE
    assert backup.size_bytes == 1024
    assert backup.label == "nightly"
    assert backup.guest_type == "lxc"
    assert backup.made_by_client is True
    assert backup.auto_delete_at == WHEN


def test_a_backup_has_only_the_optional_fields_empty_by_default() -> None:
    backup = make()

    assert backup.size_bytes is None
    assert backup.label is None
    assert backup.guest_type is None
    assert backup.made_by_client is False
    assert backup.auto_delete_at is None


def test_the_alias_is_the_first_eight_hex_characters_of_the_sha256_of_the_id() -> None:
    backup = make()

    expected = hashlib.sha256(VOLID.encode()).hexdigest()[:8]

    assert backup.alias() == expected
    assert len(backup.alias()) == ALIAS_LENGTH == 8
    assert all(c in "0123456789abcdef" for c in backup.alias())


def test_the_alias_is_computed_not_stored_and_follows_the_identifier() -> None:
    assert "alias" not in {f.name for f in fields(Backup)}
    assert make(backup_id="a").alias() != make(backup_id="b").alias()
    assert make(backup_id="a").alias() == make(backup_id="a", label="x").alias()


def test_a_backup_is_immutable() -> None:
    backup = make()

    with pytest.raises(FrozenInstanceError):
        backup.backup_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize("field_name", ["backup_id", "location", "label"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_text_is_refused(field_name: str, blank: str) -> None:
    with pytest.raises(EmptyValueError):
        make(**{field_name: blank})


@pytest.mark.parametrize("field_name", ["created_at", "auto_delete_at"])
def test_a_naive_time_is_refused(field_name: str) -> None:
    with pytest.raises(NaiveTimestampError):
        make(**{field_name: datetime(2026, 10, 25, 12, 0)})


def test_a_negative_size_is_refused_and_zero_is_valid() -> None:
    assert make(size_bytes=0).size_bytes == 0
    with pytest.raises(InvalidBackupError):
        make(size_bytes=-1)


@pytest.mark.parametrize("guest_type", ["lxc", "qemu"])
def test_the_two_guest_types_are_valid(guest_type: str) -> None:
    assert make(guest_type=guest_type).guest_type == guest_type


def test_another_guest_type_is_refused() -> None:
    with pytest.raises(InvalidBackupError):
        make(guest_type="kvm")


def make_record(**changes: object) -> BackupRecord:
    values: dict[str, object] = {
        "configuration_id": ConfigurationId("cfg-1"),
        "server_identifier": "105",
        "backup_id": VOLID,
        "kind": BackupKind.ARCHIVE,
        "location": "storage local",
        "created_at": WHEN,
        "operation_id": OperationId("op-1"),
    }
    return BackupRecord(**{**values, **changes})  # type: ignore[arg-type]


def test_a_record_holds_where_a_backup_is_kept_and_the_operation_that_made_it() -> None:
    record = make_record()

    assert record.operation_id == "op-1"
    assert record.backup_id == VOLID
    assert {f.name for f in fields(BackupRecord)} == {
        "configuration_id",
        "server_identifier",
        "backup_id",
        "kind",
        "location",
        "created_at",
        "operation_id",
    }


@pytest.mark.parametrize(
    "field_name",
    ["configuration_id", "server_identifier", "backup_id", "location", "operation_id"],
)
def test_a_record_refuses_a_blank_text(field_name: str) -> None:
    with pytest.raises(EmptyValueError):
        make_record(**{field_name: " "})


def test_a_record_refuses_a_naive_time() -> None:
    with pytest.raises(NaiveTimestampError):
        make_record(created_at=datetime(2026, 10, 25, 12, 0))


def test_a_restore_effect_holds_the_text_and_the_deleted_backups() -> None:
    deleted = (make(), make(backup_id="other"))

    effect = RestoreEffect("It overwrites the guest.", deleted)

    assert effect.consequence == "It overwrites the guest."
    assert effect.deleted_backups == deleted
    assert RestoreEffect("text").deleted_backups == ()


def test_a_restore_effect_text_is_bounded_like_the_consequence_of_an_operation() -> (
    None
):
    assert RestoreEffect("x" * MAX_CONSEQUENCE_LENGTH)
    with pytest.raises(OperationTextTooLongError):
        RestoreEffect("x" * (MAX_CONSEQUENCE_LENGTH + 1))
    with pytest.raises(EmptyValueError):
        RestoreEffect(" ")
