"""UC-019: preview, request and complete a restore with fakes."""

import pytest

from iac_node_forge.application.backup_dto import RestoreOutcome, RestoreResult
from iac_node_forge.application.dto import Confirmation
from iac_node_forge.application.errors import (
    ProviderAuthenticationError,
    ProviderUnreachableError,
)
from iac_node_forge.application.ports import BackupBlock
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.operation import (
    Operation,
    OperationId,
    OperationState,
)
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.server import Server
from tests.unit.application.fakes import FakeBackupProvider
from tests.unit.application.samples import LAB, WEB
from tests.unit.application.use_cases.backup_setup import (
    MANAGED_DB,
    Setup,
    build,
)


def typed(identifier: str = "101", again: str | None = None) -> Confirmation:
    return Confirmation(True, identifier, again)


def restore_db(
    setup: Setup, backup_id: str, again: str | None = "101"
) -> RestoreResult:
    return setup.restore.request_restore("Lab", "101", backup_id, typed("101", again))


class _MismatchedProvider(FakeBackupProvider):
    """A provider that says every backup belongs to another guest."""

    def restore_block(
        self, configuration: ProviderConfiguration, server: Server, backup: Backup
    ) -> BackupBlock | None:
        return BackupBlock.MISMATCH


class _Unreadable(FakeBackupProvider):
    """A provider whose servers can no longer be read once ``dark`` is set."""

    dark = False

    def get_server(
        self, configuration: ProviderConfiguration, server_identifier: str
    ) -> Server:
        if self.dark:
            raise ProviderUnreachableError("connection refused")
        return super().get_server(configuration, server_identifier)


def test_the_preview_states_the_backup_and_what_the_restore_replaces() -> None:
    setup = build(managed=(MANAGED_DB,))
    backup = setup.made_backup()

    preview = setup.restore.preview_restore("Lab", "101", backup.backup_id)

    assert preview.outcome is RestoreOutcome.READY
    assert (preview.server_identifier, preview.server_name) == ("101", "db")
    assert preview.server_state == "stopped"
    assert preview.managed
    assert preview.backup is not None
    assert preview.backup.backup_id == backup.backup_id
    assert preview.backup.alias == backup.alias()
    assert "cannot be undone" in preview.consequence
    assert preview.deleted_backups == ()
    assert len(setup.fake.requests) == 1  # only the backup made for the test


def test_the_backup_can_be_named_by_its_unique_alias() -> None:
    setup = build()
    backup = setup.made_backup()

    preview = setup.restore.preview_restore("Lab", "101", backup.alias())

    assert preview.outcome is RestoreOutcome.READY
    assert preview.backup is not None
    assert preview.backup.backup_id == backup.backup_id


def test_a_snapshot_restore_lists_the_newer_snapshots_it_deletes() -> None:
    setup = build(BackupKind.SNAPSHOT)
    first = setup.made_backup()
    setup.clock.sleep(3600)
    second = setup.made_backup()
    setup.clock.sleep(3600)
    third = setup.made_backup()

    preview = setup.restore.preview_restore("Lab", "101", first.backup_id)

    assert preview.outcome is RestoreOutcome.READY
    assert [b.backup_id for b in preview.deleted_backups] == [
        second.backup_id,
        third.backup_id,
    ]


def test_an_unsupported_provider_is_reported_before_the_server_is_read() -> None:
    setup = build(supported=False)

    preview = setup.restore.preview_restore("Lab", "101", "x")
    result = setup.restore.request_restore("Lab", "101", "x", typed())

    assert preview.outcome is RestoreOutcome.UNSUPPORTED
    assert "restore_server" in preview.reason
    assert result.outcome is RestoreOutcome.UNSUPPORTED
    assert setup.fake.requests == []
    assert "list_backups" not in setup.fake.reads


def test_a_server_the_provider_does_not_have_is_not_found() -> None:
    setup = build()

    result = setup.restore.request_restore("Lab", "999", "x", typed("999", "999"))

    assert result.outcome is RestoreOutcome.NOT_FOUND
    assert setup.fake.requests == []


def test_a_backup_that_is_not_listed_is_not_found() -> None:
    setup = build()
    setup.made_backup()
    before = len(setup.fake.requests)

    result = restore_db(setup, "nothing-like-it")

    assert result.outcome is RestoreOutcome.BACKUP_NOT_FOUND
    assert "full-backups" in result.reason
    assert len(setup.fake.requests) == before
    assert len(setup.operations.operations) == 1


def test_a_backup_of_another_server_is_not_found_for_this_one() -> None:
    setup = build()
    setup.made_backup("100")

    result = restore_db(setup, setup.fake.list_backups(LAB, WEB)[0].backup_id)

    assert result.outcome is RestoreOutcome.BACKUP_NOT_FOUND


def test_a_running_guest_is_refused_and_not_stopped() -> None:
    setup = build()
    backup = setup.made_backup("100")

    preview = setup.restore.preview_restore("Lab", "100", backup.backup_id)
    result = setup.restore.request_restore(
        "Lab", "100", backup.backup_id, typed("100", "100")
    )

    assert preview.outcome is RestoreOutcome.RUNNING_REFUSED
    assert "shut down" in preview.reason
    assert preview.consequence == ""
    assert result.outcome is RestoreOutcome.RUNNING_REFUSED
    assert [r for r in setup.fake.requests if r[0] == "restore"] == []
    assert result.operation is None


def test_a_backup_of_another_guest_is_a_mismatch() -> None:
    setup = build(provider_class=_MismatchedProvider)
    backup = setup.made_backup()

    result = restore_db(setup, backup.backup_id)

    assert result.outcome is RestoreOutcome.BACKUP_MISMATCH
    assert result.operation is None


def test_a_cancelled_server_is_not_available() -> None:
    setup = build(BackupKind.SNAPSHOT)
    held = setup.add_foreign("300", "old-snapshot", hours=-1)

    result = setup.restore.request_restore(
        "Lab", "300", held.backup_id, typed("300", "300")
    )

    assert result.outcome is RestoreOutcome.NOT_AVAILABLE
    assert [r for r in setup.fake.requests if r[0] == "restore"] == []


@pytest.mark.parametrize(
    ("confirmation", "outcome"),
    [
        (Confirmation(False, "101", "101"), RestoreOutcome.NOT_CONFIRMED),
        (Confirmation(True), RestoreOutcome.NOT_CONFIRMED),
        (Confirmation(True, "db", "db"), RestoreOutcome.IDENTIFIER_MISMATCH),
        (Confirmation(True, "10", "10"), RestoreOutcome.IDENTIFIER_MISMATCH),
        (Confirmation(True, "101 ", "101"), RestoreOutcome.IDENTIFIER_MISMATCH),
        (Confirmation(True, "101"), RestoreOutcome.NOT_CONFIRMED),
        (Confirmation(True, "101", "102"), RestoreOutcome.IDENTIFIER_MISMATCH),
    ],
)
def test_an_incomplete_or_wrong_confirmation_sends_and_records_nothing(
    confirmation: Confirmation, outcome: RestoreOutcome
) -> None:
    setup = build()
    backup = setup.made_backup()
    recorded = len(setup.operations.saved)

    result = setup.restore.request_restore("Lab", "101", backup.backup_id, confirmation)

    assert result.outcome is outcome
    assert result.operation is None
    assert [r for r in setup.fake.requests if r[0] == "restore"] == []
    assert len(setup.operations.saved) == recorded


def test_a_managed_server_needs_the_identifier_typed_once() -> None:
    setup = build(managed=(MANAGED_DB,))
    backup = setup.made_backup()

    wrong = setup.restore.request_restore("Lab", "101", backup.backup_id, typed("x"))
    right = setup.restore.request_restore("Lab", "101", backup.backup_id, typed())

    assert wrong.outcome is RestoreOutcome.IDENTIFIER_MISMATCH
    assert right.outcome is RestoreOutcome.REQUESTED


def test_the_request_records_a_running_operation_with_the_consequence() -> None:
    setup = build()
    backup = setup.made_backup()
    preview = setup.restore.preview_restore("Lab", "101", backup.backup_id)

    result = restore_db(setup, backup.backup_id)

    assert result.outcome is RestoreOutcome.REQUESTED
    assert result.operation is not None
    assert result.operation.action is PowerAction.RESTORE
    assert result.operation.state is OperationState.RUNNING
    assert result.consequence == preview.consequence
    stored = setup.operations.load(OperationId(result.operation.operation_id))
    assert stored.consequence == preview.consequence
    assert stored.provider_reference == backup.backup_id
    assert [r for r in setup.fake.requests if r[0] == "restore"] == [("restore", "101")]


def test_a_rejected_request_is_a_failed_operation_and_is_not_repeated() -> None:
    setup = build()
    backup = setup.made_backup()
    setup.fake.reject_with(ProviderAuthenticationError("VM.Backup is missing"))

    result = restore_db(setup, backup.backup_id)

    assert result.outcome is RestoreOutcome.REJECTED
    assert "VM.Backup" in result.reason
    assert result.operation is not None
    assert result.operation.state is OperationState.FAILED
    assert result.consequence
    assert [r for r in setup.fake.requests if r[0] == "restore"] == []


def test_a_lost_answer_is_an_unknown_operation_and_is_not_repeated() -> None:
    setup = build()
    backup = setup.made_backup()
    setup.fake.answer_unknown()

    result = restore_db(setup, backup.backup_id)

    assert result.outcome is RestoreOutcome.REQUESTED
    assert result.operation is not None
    assert result.operation.state is OperationState.UNKNOWN
    assert [r for r in setup.fake.requests if r[0] == "restore"] == [("restore", "101")]


def _requested(setup: Setup) -> str:
    backup = setup.made_backup()
    result = restore_db(setup, backup.backup_id)
    assert result.operation is not None
    return result.operation.operation_id


def test_a_completed_restore_reports_and_records_the_state_of_the_server() -> None:
    setup = build()
    operation_id = _requested(setup)
    setup.finish(operation_id)

    result = setup.restore.complete_restore(operation_id)

    assert result.outcome is RestoreOutcome.COMPLETED
    assert result.state_after == "stopped"
    assert result.consequence
    stored = setup.operations.load(OperationId(operation_id))
    assert stored.detail == "server state after the restore: stopped"
    assert result.operation is not None
    assert result.operation.detail == stored.detail


def test_completing_again_changes_nothing_more() -> None:
    setup = build()
    operation_id = _requested(setup)
    setup.finish(operation_id)
    setup.restore.complete_restore(operation_id)
    saved = len(setup.operations.saved)

    again = setup.restore.complete_restore(operation_id)

    assert again.outcome is RestoreOutcome.COMPLETED
    assert len(setup.operations.saved) == saved


def test_the_state_is_put_before_an_existing_detail() -> None:
    setup = build()
    operation_id = _requested(setup)
    setup.finish(operation_id)
    ended = setup.operations.load(OperationId(operation_id))
    setup.operations.save(
        Operation(
            ended.operation_id,
            ended.configuration_id,
            ended.server_identifier,
            ended.action,
            ended.state,
            ended.started_at,
            ended.finished_at,
            ended.provider_reference,
            "the instance is stopped",
            ended.consequence,
        )
    )

    setup.restore.complete_restore(operation_id)

    detail = setup.operations.load(OperationId(operation_id)).detail
    assert detail == "server state after the restore: stopped; the instance is stopped"


def test_a_restore_whose_state_cannot_be_read_is_still_completed() -> None:
    setup = build(provider_class=_Unreadable)
    operation_id = _requested(setup)
    setup.finish(operation_id)
    assert isinstance(setup.fake, _Unreadable)
    setup.fake.dark = True

    result = setup.restore.complete_restore(operation_id)

    assert result.outcome is RestoreOutcome.COMPLETED
    assert result.state_after is None
    assert "could not be read" in result.reason
    assert setup.operations.load(OperationId(operation_id)).detail is None


def test_a_failed_restore_reports_the_state_left() -> None:
    setup = build()
    backup = setup.made_backup()
    setup.fake.make_operations_fail()
    result = restore_db(setup, backup.backup_id)
    assert result.operation is not None
    setup.finish(result.operation.operation_id)

    completed = setup.restore.complete_restore(result.operation.operation_id)

    assert completed.outcome is RestoreOutcome.REJECTED
    assert "failed" in completed.reason
    assert completed.state_after == "stopped"


def test_an_unknown_restore_changes_nothing_and_reports_the_state() -> None:
    setup = build()
    backup = setup.made_backup()
    setup.fake.answer_unknown()
    result = restore_db(setup, backup.backup_id)
    assert result.operation is not None
    saved = len(setup.operations.saved)

    completed = setup.restore.complete_restore(result.operation.operation_id)

    assert completed.outcome is RestoreOutcome.INCOMPLETE
    assert "unknown" in completed.reason
    assert completed.state_after == "stopped"
    assert len(setup.operations.saved) == saved


def test_a_running_restore_is_not_finished() -> None:
    setup = build()
    operation_id = _requested(setup)

    result = setup.restore.complete_restore(operation_id)

    assert result.outcome is RestoreOutcome.INCOMPLETE
    assert "still running" in result.reason
    assert result.state_after is None


def test_no_operation_or_another_action_is_not_found() -> None:
    setup = build()
    made = setup.back_up.request_backup("Lab", "101", None, Confirmation(True))
    assert made.operation is not None

    missing = setup.restore.complete_restore("nope")
    wrong = setup.restore.complete_restore(made.operation.operation_id)

    assert missing.outcome is RestoreOutcome.NOT_FOUND
    assert wrong.outcome is RestoreOutcome.NOT_FOUND
    assert "not a restore" in wrong.reason
