"""UC-018: preview, request and complete a backup with fakes."""

import pytest

from iac_node_forge.application.backup_dto import BackupOutcome
from iac_node_forge.application.dto import Confirmation
from iac_node_forge.application.errors import (
    ConfigurationNotFoundError,
    ProviderAuthenticationError,
    ProviderUnreachableError,
)
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.operation import Operation, OperationId, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from tests.unit.application.fakes import FakeBackupProvider
from tests.unit.application.samples import LAB_ID
from tests.unit.application.use_cases.backup_setup import Setup, build

YES = Confirmation(given=True)


def test_the_preview_states_the_kind_the_place_and_the_consequence() -> None:
    setup = build()

    preview = setup.back_up.preview_backup("Lab", "101", None)

    assert preview.outcome is BackupOutcome.READY
    assert (preview.provider_name, preview.configuration_id) == ("Lab", LAB_ID)
    assert (preview.server_identifier, preview.server_name) == ("101", "db")
    assert preview.server_state == "stopped"
    assert preview.kind is BackupKind.ARCHIVE
    assert preview.storage == "local"
    assert preview.location == "storage local"
    assert "archive" in preview.consequence
    assert setup.fake.requests == []
    assert setup.operations.operations == {}
    assert setup.backups.records == {}


def test_a_snapshot_has_no_storage_and_is_kept_by_the_provider() -> None:
    setup = build(BackupKind.SNAPSHOT)

    preview = setup.back_up.preview_backup("Lab", "101", None)

    assert preview.outcome is BackupOutcome.READY
    assert preview.kind is BackupKind.SNAPSHOT
    assert preview.storage is None
    assert "provider" in preview.location


def test_an_unsupported_provider_is_reported_before_the_server_is_read() -> None:
    setup = build(supported=False)

    preview = setup.back_up.preview_backup("Lab", "101", None)
    result = setup.back_up.request_backup("Lab", "101", None, YES)

    assert preview.outcome is BackupOutcome.UNSUPPORTED
    assert "backup_server" in preview.reason
    assert result.outcome is BackupOutcome.UNSUPPORTED
    assert setup.fake.requests == []
    assert "backup_block" not in setup.fake.reads
    assert setup.operations.operations == {}


def test_a_server_the_provider_does_not_have_is_not_found() -> None:
    setup = build()

    result = setup.back_up.request_backup("Lab", "999", None, YES)

    assert result.outcome is BackupOutcome.NOT_FOUND
    assert result.operation is None
    assert setup.fake.requests == []


def test_an_unknown_configuration_is_an_error() -> None:
    setup = build()

    with pytest.raises(ConfigurationNotFoundError):
        setup.back_up.preview_backup("Nowhere", "101", None)


def test_several_storages_must_be_chosen_and_are_listed() -> None:
    setup = build(storages=("local", "backups"))

    preview = setup.back_up.preview_backup("Lab", "101", None)
    result = setup.back_up.request_backup("Lab", "101", None, YES)

    assert preview.outcome is BackupOutcome.STORAGE_REQUIRED
    assert preview.storages == ("local", "backups")
    assert "local, backups" in preview.reason
    assert result.outcome is BackupOutcome.STORAGE_REQUIRED
    assert setup.fake.requests == []


def test_a_named_storage_is_used_among_several() -> None:
    setup = build(storages=("local", "backups"))

    preview = setup.back_up.preview_backup("Lab", "101", "backups")

    assert preview.outcome is BackupOutcome.READY
    assert preview.storage == "backups"
    assert preview.location == "storage backups"


def test_a_server_with_no_storage_that_accepts_backups_is_refused() -> None:
    setup = build()
    setup.fake.remove_storages()

    result = setup.back_up.request_backup("Lab", "101", None, YES)

    assert result.outcome is BackupOutcome.NO_BACKUP_STORAGE
    assert setup.fake.requests == []
    assert setup.operations.operations == {}


@pytest.mark.parametrize("kind", [BackupKind.ARCHIVE, BackupKind.SNAPSHOT])
def test_a_storage_the_provider_cannot_use_is_unsuitable(kind: BackupKind) -> None:
    setup = build(kind)

    preview = setup.back_up.preview_backup("Lab", "101", "nowhere")

    assert preview.outcome is BackupOutcome.STORAGE_UNSUITABLE
    assert "nowhere" in preview.reason
    assert preview.consequence == ""


def test_a_cancelled_server_is_not_available_for_a_snapshot() -> None:
    setup = build(BackupKind.SNAPSHOT)

    result = setup.back_up.request_backup("Lab", "300", None, YES)

    assert result.outcome is BackupOutcome.NOT_AVAILABLE
    assert setup.fake.requests == []


def test_nothing_is_sent_or_recorded_without_the_confirmation() -> None:
    setup = build()

    result = setup.back_up.request_backup("Lab", "101", None, Confirmation(False))

    assert result.outcome is BackupOutcome.NOT_CONFIRMED
    assert result.operation is None
    assert setup.fake.requests == []
    assert setup.operations.operations == {}


def test_a_plain_confirmation_is_enough_even_for_an_unmanaged_server() -> None:
    setup = build()

    result = setup.back_up.request_backup("Lab", "101", None, YES)

    assert result.outcome is BackupOutcome.REQUESTED
    assert setup.fake.requests == [("backup", "101")]


def test_the_request_records_a_running_operation_with_the_consequence() -> None:
    setup = build()

    result = setup.back_up.request_backup("Lab", "101", None, YES)

    assert result.operation is not None
    assert result.operation.action is PowerAction.BACKUP
    assert result.operation.state is OperationState.RUNNING
    stored = setup.operations.load(OperationId(result.operation.operation_id))
    assert stored.consequence == result.consequence
    assert "archive" in result.consequence
    assert setup.backups.records == {}
    assert len(setup.fake.requests) == 1


def test_a_rejected_request_is_a_failed_operation_and_is_not_repeated() -> None:
    setup = build()
    setup.fake.reject_with(ProviderAuthenticationError("VM.Backup is missing"))

    result = setup.back_up.request_backup("Lab", "101", None, YES)

    assert result.outcome is BackupOutcome.REJECTED
    assert "VM.Backup" in result.reason
    assert result.operation is not None
    assert result.operation.state is OperationState.FAILED
    assert result.consequence
    assert result.state_left is not None
    assert len(setup.operations.saved) == 1
    assert setup.backups.records == {}


def test_a_lost_answer_is_an_unknown_operation_and_is_not_repeated() -> None:
    setup = build()
    setup.fake.answer_unknown()

    result = setup.back_up.request_backup("Lab", "101", None, YES)

    assert result.outcome is BackupOutcome.REQUESTED
    assert result.operation is not None
    assert result.operation.state is OperationState.UNKNOWN
    assert setup.fake.requests == [("backup", "101")]


def _requested(setup: Setup) -> str:
    result = setup.back_up.request_backup("Lab", "101", None, YES)
    assert result.operation is not None
    return result.operation.operation_id


def test_a_succeeded_backup_is_recorded_and_its_place_is_reported() -> None:
    setup = build()
    operation_id = _requested(setup)
    setup.finish(operation_id)

    result = setup.back_up.complete_backup(operation_id)

    assert result.outcome is BackupOutcome.COMPLETED
    assert result.recorded
    assert result.backup is not None
    assert result.backup.location.startswith("storage local")
    assert result.backup.operation_id == operation_id
    [record] = setup.backups.saved
    assert record.operation_id == operation_id
    assert record.backup_id == result.backup.backup_id
    assert (record.kind, record.location) == (
        BackupKind.ARCHIVE,
        result.backup.location,
    )
    assert setup.backups.load_by_operation(record.operation_id) == record


def test_completing_again_records_no_second_backup() -> None:
    setup = build()
    operation_id = _requested(setup)
    setup.finish(operation_id)
    first = setup.back_up.complete_backup(operation_id)

    second = setup.back_up.complete_backup(operation_id)

    assert second.outcome is BackupOutcome.COMPLETED
    assert second.backup is not None
    assert first.backup is not None
    assert second.backup.backup_id == first.backup.backup_id
    assert len(setup.backups.saved) == 1


def test_a_succeeded_operation_whose_backup_is_not_listed_records_nothing() -> None:
    setup = build()
    setup.fake.hide_backups = True
    operation_id = _requested(setup)
    setup.finish(operation_id)

    result = setup.back_up.complete_backup(operation_id)

    assert result.outcome is BackupOutcome.INCOMPLETE
    assert "could not be located" in result.reason
    assert result.backup is None
    assert not result.recorded
    assert setup.backups.saved == []


class _Unreachable(FakeBackupProvider):
    """A provider that cannot be asked to find the backup an operation made."""

    def find_backup(
        self, configuration: ProviderConfiguration, operation: Operation
    ) -> Backup | None:
        raise ProviderUnreachableError("connection refused")


def test_a_provider_that_cannot_be_asked_to_locate_the_backup_records_nothing() -> None:
    setup = build(provider_class=_Unreachable)
    operation_id = _requested(setup)
    setup.finish(operation_id)

    result = setup.back_up.complete_backup(operation_id)

    assert result.outcome is BackupOutcome.INCOMPLETE
    assert "connection refused" in result.reason
    assert setup.backups.saved == []


def test_a_failed_operation_records_nothing_and_reports_what_the_provider_holds() -> (
    None
):
    setup = build()
    setup.fake.make_operations_fail()
    operation_id = _requested(setup)
    setup.finish(operation_id)

    result = setup.back_up.complete_backup(operation_id)

    assert result.outcome is BackupOutcome.REJECTED
    assert "failed" in result.reason
    assert result.state_left is not None
    assert "0 backup" in result.state_left
    assert setup.backups.saved == []


def test_an_unknown_operation_changes_nothing() -> None:
    setup = build()
    setup.fake.answer_unknown()
    operation_id = _requested(setup)

    result = setup.back_up.complete_backup(operation_id)

    assert result.outcome is BackupOutcome.INCOMPLETE
    assert "unknown" in result.reason
    assert setup.backups.saved == []


def test_a_running_operation_is_not_finished() -> None:
    setup = build()
    operation_id = _requested(setup)

    result = setup.back_up.complete_backup(operation_id)

    assert result.outcome is BackupOutcome.INCOMPLETE
    assert "still running" in result.reason


def test_no_operation_or_another_action_is_not_found() -> None:
    setup = build()
    made = setup.made_backup()
    restore = setup.restore.request_restore(
        "Lab", "101", made.backup_id, Confirmation(True, "101", "101")
    )
    assert restore.operation is not None

    missing = setup.back_up.complete_backup("nope")
    wrong_action = setup.back_up.complete_backup(restore.operation.operation_id)

    assert missing.outcome is BackupOutcome.NOT_FOUND
    assert wrong_action.outcome is BackupOutcome.NOT_FOUND
    assert "not a backup" in wrong_action.reason
