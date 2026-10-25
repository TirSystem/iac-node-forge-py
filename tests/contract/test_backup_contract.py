"""Backup and restore behaviours every ServerBackupPort implementation shares.

Each test runs against FakeBackupProvider, ProxmoxProvider plus its mock and
ContaboProvider plus its mock, which shows that the fake and both adapters
agree (ADR-0010, MIL-008 Go criterion 2). No variant is skipped.
"""

import logging

import pytest

from iac_node_forge.application.capabilities import BACKUP_SERVER, RESTORE_SERVER
from iac_node_forge.application.ports import BackupBlock, ServerBackupPort
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.operation import (
    MAX_CONSEQUENCE_LENGTH,
    MAX_DETAIL_LENGTH,
    Operation,
    OperationState,
)
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.server import Server
from tests.contract.provider_under_test import BackupCase

MAX_POLLS = 20


def read(case: BackupCase, identifier: str | None = None) -> Server:
    """Read a server the way the use case will, before any request."""
    return case.provider.get_server(case.configuration, identifier or case.server_id)


def back_up(case: BackupCase) -> Operation:
    """Back up server 101 the way the use case will: read first, then one write."""
    return case.backup.request_backup(case.configuration, read(case), case.storage)


def restore(case: BackupCase, backup: Backup) -> Operation:
    return case.backup.request_restore(case.configuration, read(case), backup)


def follow(case: BackupCase, operation: Operation) -> list[Operation]:
    """Ask for the operation until it ends; the poll loop belongs to the use case."""
    seen: list[Operation] = []
    for _ in range(MAX_POLLS):
        operation = case.backup.operation_state(case.configuration, operation)
        seen.append(operation)
        if operation.has_ended:
            break
    return seen


def made_backup(case: BackupCase) -> Backup:
    """Back the server up, follow the operation and return the backup it made."""
    operation = back_up(case)
    final = follow(case, operation)[-1]
    assert final.state is OperationState.SUCCEEDED
    backup = case.backup.find_backup(case.configuration, final)
    assert backup is not None
    return backup


def listed(case: BackupCase) -> tuple[Backup, ...]:
    return case.backup.list_backups(case.configuration, read(case))


def test_provider_satisfies_server_backup_port(backup_case: BackupCase) -> None:
    assert isinstance(backup_case.backup, ServerBackupPort)


def test_backup_and_restore_capabilities_are_declared_by_the_provider(
    backup_case: BackupCase,
) -> None:
    case = backup_case

    names = {c.name for c in case.provider.discover_capabilities(case.configuration)}

    assert {BACKUP_SERVER, RESTORE_SERVER} <= names


def test_the_backup_kind_is_the_one_the_provider_means_by_a_backup(
    backup_case: BackupCase,
) -> None:
    case = backup_case

    assert case.backup.backup_kind(case.configuration) is case.kind


def test_the_storages_are_those_that_accept_backups(backup_case: BackupCase) -> None:
    case = backup_case

    storages = case.backup.backup_storages(case.configuration, read(case))

    assert storages == case.expected_storages


def test_a_stopped_server_with_a_suitable_storage_is_not_blocked(
    backup_case: BackupCase,
) -> None:
    case = backup_case

    block = case.backup.backup_block(case.configuration, read(case), case.storage)

    assert block is None


def test_a_storage_that_cannot_hold_a_backup_is_refused(
    backup_case: BackupCase,
) -> None:
    case = backup_case

    block = case.backup.backup_block(
        case.configuration, read(case), case.unsuitable_storage
    )

    assert block is BackupBlock.STORAGE_UNSUITABLE


def test_no_storage_named_and_none_available_is_reported_as_the_provider_does(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    case.remove_storages()

    block = case.backup.backup_block(case.configuration, read(case), None)

    assert block is case.expected_no_storage_block


def test_a_backup_of_a_running_server_is_allowed(backup_case: BackupCase) -> None:
    case = backup_case

    block = case.backup.backup_block(
        case.configuration, read(case, case.blocked_id), case.storage
    )

    assert block is None


def test_a_restore_the_provider_blocks_is_reported_with_its_block(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    backup = made_backup(case)
    case.prepare_blocked()

    block = case.backup.restore_block(
        case.configuration, read(case, case.blocked_id), backup
    )

    assert block is case.expected_restore_block


def test_a_stopped_server_is_not_blocked_for_the_restore_of_its_own_backup(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    backup = made_backup(case)

    block = case.backup.restore_block(case.configuration, read(case), backup)

    assert block is None


def test_a_backup_that_is_not_of_this_server_is_a_mismatch(
    backup_case: BackupCase,
) -> None:
    case = backup_case

    block = case.backup.restore_block(
        case.configuration, read(case), case.foreign_backup
    )

    assert block is BackupBlock.MISMATCH


def test_the_backup_consequence_is_a_bounded_text_that_names_the_kind_and_server(
    backup_case: BackupCase,
) -> None:
    case = backup_case

    text = case.backup.backup_consequence(case.configuration, read(case), case.storage)

    assert text.strip()
    assert len(text) <= MAX_CONSEQUENCE_LENGTH
    assert case.kind.value in text.lower()
    assert case.server_id in text
    assert not [v for v in case.secret_values if v in text]


def test_the_restore_effect_is_a_bounded_text_that_says_it_cannot_be_undone(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    backup = made_backup(case)

    effect = case.backup.restore_effect(case.configuration, read(case), backup)

    assert len(effect.consequence) <= MAX_CONSEQUENCE_LENGTH
    assert case.server_id in effect.consequence
    assert "cannot be undone" in effect.consequence
    assert effect.deleted_backups == ()
    assert not [v for v in case.secret_values if v in effect.consequence]


def test_the_reads_before_the_request_send_no_write(backup_case: BackupCase) -> None:
    case = backup_case
    server = read(case)

    case.backup.backup_kind(case.configuration)
    case.backup.backup_storages(case.configuration, server)
    case.backup.backup_block(case.configuration, server, case.storage)
    case.backup.restore_block(case.configuration, server, case.foreign_backup)
    case.backup.backup_consequence(case.configuration, server, case.storage)
    case.backup.restore_effect(case.configuration, server, case.foreign_backup)
    case.backup.list_backups(case.configuration, server)

    assert case.write_requests() == []


def test_backing_up_returns_a_running_backup_operation_for_the_server(
    backup_case: BackupCase,
) -> None:
    case = backup_case

    operation = back_up(case)

    assert operation.state is OperationState.RUNNING
    assert operation.action is PowerAction.BACKUP
    assert operation.server_identifier == case.server_id
    assert operation.configuration_id == case.configuration.configuration_id
    assert operation.started_at.tzinfo is not None
    assert operation.finished_at is None


def test_following_the_backup_reaches_succeeded_after_running(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    operation = back_up(case)

    seen = follow(case, operation)

    assert seen[0].state is OperationState.RUNNING
    assert seen[0].finished_at is None
    final = seen[-1]
    assert final.state is OperationState.SUCCEEDED
    assert final.action is PowerAction.BACKUP
    assert final.operation_id == operation.operation_id
    assert final.finished_at is not None
    assert final.finished_at >= final.started_at
    assert len(seen) < MAX_POLLS


def test_the_backup_is_found_again_by_the_operation_that_made_it(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    operation = back_up(case)
    final = follow(case, operation)[-1]

    backup = case.backup.find_backup(case.configuration, final)

    assert backup is not None
    assert backup.kind is case.kind
    assert backup.made_by_client is True
    assert backup.location.strip()
    assert backup.created_at.tzinfo is not None
    assert backup.alias() == backup.alias()
    assert len(backup.alias()) == 8
    assert not [v for v in case.secret_values if v in backup.location]


def test_no_backup_is_found_for_an_operation_that_made_none(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    made_backup(case)
    other = back_up(case)

    found = case.backup.find_backup(case.configuration, other)

    assert found is None


def test_the_listing_shows_the_backup_that_was_made(backup_case: BackupCase) -> None:
    case = backup_case
    before = listed(case)

    backup = made_backup(case)

    after = listed(case)
    assert backup in after
    assert len(after) == len(before) + 1
    assert [b.created_at for b in after] == sorted(b.created_at for b in after)
    assert all(b.kind is case.kind for b in after)


def test_an_ended_backup_operation_is_returned_unchanged(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    final = follow(case, back_up(case))[-1]

    again = case.backup.operation_state(case.configuration, final)

    assert again == final


def test_restoring_returns_a_running_restore_operation_for_the_server(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    backup = made_backup(case)

    operation = restore(case, backup)

    assert operation.state is OperationState.RUNNING
    assert operation.action is PowerAction.RESTORE
    assert operation.server_identifier == case.server_id
    assert operation.finished_at is None


def test_following_the_restore_reaches_succeeded_and_the_state_is_restored(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    backup = made_backup(case)
    case.change_server()

    seen = follow(case, restore(case, backup))

    final = seen[-1]
    assert final.state is OperationState.SUCCEEDED
    assert final.action is PowerAction.RESTORE
    assert final.finished_at is not None
    assert case.state_is_restored()
    assert backup in listed(case)


def test_a_restore_deletes_what_the_kind_says_and_the_effect_names_it_beforehand(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    first = made_backup(case)
    second = made_backup(case)
    deletes = case.kind is BackupKind.SNAPSHOT

    effect = case.backup.restore_effect(case.configuration, read(case), first)
    seen = follow(case, restore(case, first))

    assert seen[0].state is OperationState.RUNNING
    assert seen[-1].state is OperationState.SUCCEEDED
    assert effect.deleted_backups == ((second,) if deletes else ())
    assert (second in listed(case)) is not deletes
    assert first in listed(case)
    assert len(effect.consequence) <= MAX_CONSEQUENCE_LENGTH
    if deletes:
        assert (second.label or second.backup_id) in effect.consequence


def test_a_rejected_backup_gives_failed_with_a_reason_and_no_secret(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    before = listed(case)
    case.reject_backup()

    operation = back_up(case)

    assert operation.state is OperationState.FAILED
    assert operation.action is PowerAction.BACKUP
    assert operation.server_identifier == case.server_id
    assert operation.detail
    assert len(operation.detail) <= MAX_DETAIL_LENGTH
    assert operation.finished_at is not None
    assert not [v for v in case.secret_values if v in operation.detail]
    if case.rejection_hint is not None:
        assert case.rejection_hint in operation.detail
    assert listed(case) == before


def test_a_rejected_restore_gives_failed_with_a_reason_and_no_secret(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    backup = made_backup(case)
    case.change_server()
    case.reject_restore()

    operation = restore(case, backup)

    assert operation.state is OperationState.FAILED
    assert operation.action is PowerAction.RESTORE
    assert operation.detail
    assert len(operation.detail) <= MAX_DETAIL_LENGTH
    assert not [v for v in case.secret_values if v in operation.detail]
    if case.rejection_hint is not None:
        assert case.rejection_hint in operation.detail
    assert not case.state_is_restored()


def test_a_backup_the_provider_ends_in_failure_is_failed_with_a_reason(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    case.fail_operations()

    final = follow(case, back_up(case))[-1]

    assert final.state is OperationState.FAILED
    assert final.detail
    assert final.finished_at is not None
    assert not [v for v in case.secret_values if v in final.detail]


def test_a_lost_answer_to_the_backup_gives_unknown_and_is_not_repeated(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    case.lose_answers()

    operation = back_up(case)

    assert operation.state is OperationState.UNKNOWN
    assert operation.action is PowerAction.BACKUP
    assert operation.server_identifier == case.server_id
    assert operation.detail
    assert "may have reached the provider" in operation.detail
    assert operation.finished_at is not None
    assert len(case.write_requests()) <= 1


def test_a_lost_answer_to_the_restore_gives_unknown_and_is_not_repeated(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    backup = made_backup(case)
    written = len(case.write_requests())
    case.lose_answers()

    operation = restore(case, backup)

    assert operation.state is OperationState.UNKNOWN
    assert operation.action is PowerAction.RESTORE
    assert "may have reached the provider" in (operation.detail or "")
    assert len(case.write_requests()) <= written + 1


def test_the_only_writes_received_are_the_one_backup_and_the_one_restore(
    backup_case: BackupCase,
) -> None:
    case = backup_case
    backup = made_backup(case)
    follow(case, restore(case, backup))

    written = case.write_requests()

    assert len(written) == 2
    assert set(written) <= case.allowed_write_requests


def test_secret_values_never_appear_in_operations_errors_or_logs(
    backup_case: BackupCase, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    case = backup_case
    server = read(case)
    texts: list[str] = [repr(case.backup)]
    texts.append(
        case.backup.backup_consequence(case.configuration, server, case.storage)
    )
    operation = back_up(case)
    steps = follow(case, operation)
    texts.extend(_texts_of(step) for step in [operation, *steps])
    backup = case.backup.find_backup(case.configuration, steps[-1])
    assert backup is not None
    texts.append(repr(backup))
    effect = case.backup.restore_effect(case.configuration, server, backup)
    texts.append(effect.consequence)
    restoring = restore(case, backup)
    texts.extend(_texts_of(step) for step in [restoring, *follow(case, restoring)])
    case.reject_backup()
    texts.append(_texts_of(back_up(case)))
    texts.extend(record.getMessage() for record in caplog.records)

    leaked = [v for v in case.secret_values if any(v in text for text in texts)]
    assert texts
    assert not leaked


def _texts_of(operation: Operation) -> str:
    return " | ".join(
        [
            repr(operation),
            str(operation),
            operation.detail or "",
            operation.provider_reference or "",
            operation.consequence or "",
        ]
    )
