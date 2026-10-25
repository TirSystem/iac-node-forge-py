"""Backup and restore behaviours specific to the Contabo adapter (mock API)."""

import inspect
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from iac_node_forge.application.errors import (
    ProviderError,
    ServerNotFoundError,
)
from iac_node_forge.application.ports import BackupBlock
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.operation import (
    MAX_CONSEQUENCE_LENGTH,
    Operation,
    OperationState,
)
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.server import Server
from iac_node_forge.infrastructure.contabo import ContaboProvider
from iac_node_forge.infrastructure.contabo import _backup as backup_module
from tests.contract.conftest import CONTABO_VALUES, contabo_configuration_for
from tests.contract.mock_contabo import MockContaboServer

MAX_POLLS = 20
NOW = datetime(2026, 10, 25, 12, 30, 45, tzinfo=UTC)
BASE = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


@pytest.fixture
def configuration(mock_contabo: MockContaboServer) -> ProviderConfiguration:
    return contabo_configuration_for(mock_contabo.address)


@pytest.fixture
def provider(mock_contabo: MockContaboServer) -> ContaboProvider:
    return ContaboProvider(
        Credentials(CONTABO_VALUES),
        auth_url=mock_contabo.auth_url,
        timeout=2.0,
        clock=lambda: NOW,
    )


def server_of(
    provider: ContaboProvider, configuration: ProviderConfiguration, identifier: str
) -> Server:
    return provider.get_server(configuration, identifier)


def back_up(
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
    identifier: str = "101",
) -> Operation:
    server = server_of(provider, configuration, identifier)
    return provider.request_backup(configuration, server, None)


def follow(
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
    operation: Operation,
) -> Operation:
    for _ in range(MAX_POLLS):
        operation = provider.operation_state(configuration, operation)
        if operation.has_ended:
            break
    return operation


def made(provider: ContaboProvider, configuration: ProviderConfiguration) -> Backup:
    final = follow(provider, configuration, back_up(provider, configuration))
    assert final.state is OperationState.SUCCEEDED
    found = provider.find_backup(configuration, final)
    assert found is not None
    return found


def seed(
    mock: MockContaboServer,
    name: str,
    hours: int,
    instance: int = 101,
    **extra: Any,
) -> dict[str, Any]:
    created = BASE + timedelta(hours=hours)
    snapshot: dict[str, Any] = {
        "snapshotId": f"seed-{name}",
        "name": name,
        "description": "made by hand",
        "instanceId": instance,
        "createdDate": created.isoformat().replace("+00:00", "Z"),
        "autoDeleteDate": (created + timedelta(days=30))
        .isoformat()
        .replace("+00:00", "Z"),
        **extra,
    }
    mock.state.snapshots.append(snapshot)
    return snapshot


def test_the_kind_is_snapshot_and_there_is_no_storage_to_choose(
    provider: ContaboProvider, configuration: ProviderConfiguration
) -> None:
    server = server_of(provider, configuration, "101")

    assert provider.backup_kind(configuration) is BackupKind.SNAPSHOT
    assert provider.backup_storages(configuration, server) == ()
    assert provider.backup_block(configuration, server, None) is None
    assert (
        provider.backup_block(configuration, server, "local")
        is BackupBlock.STORAGE_UNSUITABLE
    )


def test_a_cancelled_instance_cannot_be_backed_up_and_nothing_is_sent(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_contabo.state.instances[1]["status"] = "cancelled"
    server = server_of(provider, configuration, "101")

    block = provider.backup_block(configuration, server, None)
    operation = provider.request_backup(configuration, server, None)

    assert block is BackupBlock.CANCELLED
    assert operation.state is OperationState.FAILED
    assert BackupBlock.CANCELLED.value in (operation.detail or "")
    assert mock_contabo.write_requests == []


def test_a_named_storage_gives_failed_and_nothing_is_sent(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    server = server_of(provider, configuration, "101")

    operation = provider.request_backup(configuration, server, "local")

    assert operation.state is OperationState.FAILED
    assert mock_contabo.write_requests == []


def test_the_snapshot_is_posted_with_a_short_name_and_the_operation_in_the_description(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = back_up(provider, configuration)

    (post,) = mock_contabo.write_requests
    assert (post.method, post.path) == ("POST", "/v1/compute/instances/101/snapshots")
    assert post.request_id is not None
    (body,) = mock_contabo.state.snapshot_requests
    assert body == {
        "name": "iacnf-202610251230",
        "description": f"Created by iac-node-forge, operation {operation.operation_id}",
    }
    assert len(body["name"]) == 18
    assert operation.state is OperationState.RUNNING
    assert operation.action is PowerAction.BACKUP
    assert operation.provider_reference == mock_contabo.state.snapshots[0]["snapshotId"]


def test_the_name_has_the_documented_form_for_any_time() -> None:
    name = backup_module.snapshot_name(datetime(2026, 1, 2, 3, 4, tzinfo=UTC))

    assert name == "iacnf-202601020304"
    assert re.fullmatch(r"iacnf-\d{12}", name)
    assert len(name) <= 30


def test_an_unknown_or_non_numeric_instance_is_not_found_and_sends_nothing(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    ghost = Server("999", "ghost", "running", "", (), False)
    odd = Server("abc", "odd", "running", "", (), False)

    with pytest.raises(ServerNotFoundError):
        provider.request_backup(configuration, ghost, None)
    with pytest.raises(ServerNotFoundError):
        provider.request_backup(configuration, odd, None)

    assert [r.path for r in mock_contabo.write_requests] == [
        "/v1/compute/instances/999/snapshots"
    ]


def test_the_backup_is_followed_through_the_instance_status_and_the_listing(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_contabo.state.interim_status = "installing"
    operation = back_up(provider, configuration)

    states = []
    for _ in range(MAX_POLLS):
        operation = provider.operation_state(configuration, operation)
        states.append(operation.state)
        if operation.has_ended:
            break

    assert states[:2] == [OperationState.RUNNING, OperationState.RUNNING]
    assert states[-1] is OperationState.SUCCEEDED
    assert operation.detail == "the snapshot is listed"


def test_a_listed_snapshot_with_an_instance_that_is_not_final_is_not_success(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = back_up(provider, configuration)
    mock_contabo.state.hidden_snapshots.clear()
    mock_contabo.state.transitions.clear()
    instance = mock_contabo.state.instances[1]

    instance["status"] = "installing"
    waiting = provider.operation_state(configuration, operation)
    instance["status"] = "error"
    failed = provider.operation_state(configuration, operation)
    instance["status"] = "stopped"
    done = provider.operation_state(configuration, operation)

    assert waiting.state is OperationState.RUNNING
    assert failed.state is OperationState.FAILED
    assert "error" in (failed.detail or "")
    assert done.state is OperationState.SUCCEEDED


def test_a_snapshot_that_never_appears_stays_running(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = back_up(provider, configuration)
    mock_contabo.state.transitions.clear()

    for _ in range(5):
        operation = provider.operation_state(configuration, operation)

    assert operation.state is OperationState.RUNNING


def test_the_snapshot_is_found_by_its_description_and_described(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    seed(mock_contabo, "manual", 1)
    operation = back_up(provider, configuration)
    final = follow(provider, configuration, operation)

    backup = provider.find_backup(configuration, final)

    assert backup is not None
    assert backup.kind is BackupKind.SNAPSHOT
    assert backup.label == "iacnf-202610251230"
    assert backup.made_by_client is True
    assert backup.backup_id == operation.provider_reference
    assert "snapshot iacnf-202610251230" in backup.location
    assert backup.auto_delete_at is not None
    assert backup.auto_delete_at - backup.created_at == timedelta(days=30)
    assert backup.size_bytes is None
    assert backup.guest_type is None


def test_a_snapshot_without_dates_is_found_by_nothing_and_listed_by_no_one(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_contabo.state.snapshots_without_dates = True
    final = follow(provider, configuration, back_up(provider, configuration))
    server = server_of(provider, configuration, "101")

    assert final.state is OperationState.SUCCEEDED
    assert provider.find_backup(configuration, final) is None
    assert provider.list_backups(configuration, server) == ()


def test_a_snapshot_without_an_auto_delete_date_has_none(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_contabo.state.snapshots_without_auto_delete = True

    backup = made(provider, configuration)

    assert backup.auto_delete_at is None


def test_an_operation_without_a_server_finds_no_backup(
    provider: ContaboProvider, configuration: ProviderConfiguration
) -> None:
    operation = back_up(provider, configuration)
    nameless = Operation(
        operation.operation_id,
        operation.configuration_id,
        None,
        PowerAction.BACKUP,
        OperationState.RUNNING,
        operation.started_at,
    )

    assert provider.find_backup(configuration, nameless) is None


def test_the_listing_is_ordered_by_date_whatever_order_the_provider_gives(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_contabo.state.snapshots_newest_first = True
    seed(mock_contabo, "third", 30)
    seed(mock_contabo, "first", 10)
    seed(mock_contabo, "second", 20)
    seed(mock_contabo, "elsewhere", 5, instance=100)
    server = server_of(provider, configuration, "101")

    backups = provider.list_backups(configuration, server)

    assert [b.label for b in backups] == ["first", "second", "third"]
    assert [b.made_by_client for b in backups] == [False, False, False]


def test_the_listing_marks_the_snapshots_this_client_made(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    seed(mock_contabo, "iacnf-202601010000", 1)
    seed(mock_contabo, "manual", 2)
    server = server_of(provider, configuration, "101")

    backups = provider.list_backups(configuration, server)

    assert {b.label: b.made_by_client for b in backups} == {
        "iacnf-202601010000": True,
        "manual": False,
    }


def test_the_listing_reads_the_snapshots_of_the_instance_with_the_token_and_request_id(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    server = server_of(provider, configuration, "101")

    provider.list_backups(configuration, server)

    reads = [r for r in mock_contabo.api_requests if r.path.endswith("/snapshots")]
    assert reads
    assert all(r.request_id for r in reads)


def test_a_restore_of_a_cancelled_instance_or_a_foreign_snapshot_is_blocked(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    backup = made(provider, configuration)
    seed(mock_contabo, "elsewhere", 5, instance=100)
    other = Backup("seed-elsewhere", BackupKind.SNAPSHOT, "x", BASE, label="elsewhere")
    server = server_of(provider, configuration, "101")
    written = len(mock_contabo.write_requests)

    mismatch = provider.restore_block(configuration, server, other)
    refused = provider.request_restore(configuration, server, other)
    mock_contabo.state.instances[1]["status"] = "cancelled"
    cancelled = Server("101", "db", "cancelled", "", (), False)
    block = provider.restore_block(configuration, cancelled, backup)
    cancelled_op = provider.request_restore(configuration, cancelled, backup)

    assert mismatch is BackupBlock.MISMATCH
    assert block is BackupBlock.CANCELLED
    assert refused.state is OperationState.FAILED
    assert cancelled_op.state is OperationState.FAILED
    assert len(mock_contabo.write_requests) == written


def test_the_effect_lists_the_newer_snapshots_by_name_and_date(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    older = seed(mock_contabo, "older", 1)
    target = seed(mock_contabo, "target", 2)
    seed(mock_contabo, "newer-a", 3)
    seed(mock_contabo, "newer-b", 4)
    seed(mock_contabo, "elsewhere", 9, instance=100)
    server = server_of(provider, configuration, "101")
    backups = {b.label: b for b in provider.list_backups(configuration, server)}

    effect = provider.restore_effect(configuration, server, backups["target"])

    assert [b.label for b in effect.deleted_backups] == ["newer-a", "newer-b"]
    text = effect.consequence
    assert "Instance 101 (db) is rolled back to snapshot target" in text
    assert "(2026-09-01 10:00 UTC)" in text
    assert "newer snapshots: newer-a (2026-09-01 11:00 UTC), newer-b" in text
    assert "older" not in text
    assert "Cost is not stated" in text
    assert "cannot be undone" in text
    assert older["snapshotId"] != target["snapshotId"]


def test_the_latest_snapshot_deletes_nothing_and_the_text_says_so(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    seed(mock_contabo, "only", 1)
    server = server_of(provider, configuration, "101")
    (backup,) = provider.list_backups(configuration, server)

    effect = provider.restore_effect(configuration, server, backup)

    assert effect.deleted_backups == ()
    assert "No newer snapshot exists, so none is deleted." in effect.consequence


def test_many_newer_snapshots_are_summarised_within_the_text_bound(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    seed(mock_contabo, "x" * 30, 1)
    for number in range(10):
        seed(
            mock_contabo, f"newer-with-a-long-name-{number:02d}-" + "y" * 5, 2 + number
        )
    server = Server("101", "n" * 200, "stopped", "", (), False)
    backups = provider.list_backups(configuration, server)

    effect = provider.restore_effect(configuration, server, backups[0])

    assert len(effect.deleted_backups) == 10
    assert "and 7 more" in effect.consequence
    assert len(effect.consequence) <= MAX_CONSEQUENCE_LENGTH
    assert "cannot be undone" in effect.consequence


def test_an_undated_snapshot_makes_the_effect_unknowable(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    backup = made(provider, configuration)
    mock_contabo.state.snapshots_without_dates = True
    server = server_of(provider, configuration, "101")

    with pytest.raises(ProviderError, match="no creation date"):
        provider.restore_effect(configuration, server, backup)


def test_the_backup_consequence_says_a_snapshot_is_not_a_backup_and_cost_is_not_stated(
    provider: ContaboProvider, configuration: ProviderConfiguration
) -> None:
    server = server_of(provider, configuration, "101")

    text = provider.backup_consequence(configuration, server, None)

    assert "not an independent backup" in text
    assert "same disk system" in text
    assert "auto-delete date" in text
    assert "Cost is not stated" in text
    assert "automated-backup add-on" in text
    assert len(text) <= MAX_CONSEQUENCE_LENGTH


def test_the_rollback_posts_an_empty_body_to_the_snapshot_and_deletes_newer_ones(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    first = made(provider, configuration)
    second = made(provider, configuration)
    server = server_of(provider, configuration, "101")

    operation = provider.request_restore(configuration, server, first)

    rollback = mock_contabo.write_requests[-1]
    assert (rollback.method, rollback.path) == (
        "POST",
        f"/v1/compute/instances/101/snapshots/{first.backup_id}/rollback",
    )
    assert rollback.request_id is not None
    assert mock_contabo.state.rolled_back == [("101", first.backup_id)]
    assert operation.action is PowerAction.RESTORE
    assert operation.state is OperationState.RUNNING
    assert operation.provider_reference == first.backup_id
    final = follow(provider, configuration, operation)
    assert final.state is OperationState.SUCCEEDED
    assert final.detail == "the instance is stopped and no newer snapshot is listed"
    left = provider.list_backups(configuration, server)
    assert [b.backup_id for b in left] == [first.backup_id]
    assert second.backup_id not in {b.backup_id for b in left}


def test_a_rollback_that_leaves_newer_snapshots_is_never_reported_as_success(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    first = made(provider, configuration)
    made(provider, configuration)
    mock_contabo.state.rollback_keeps_newer = True
    server = server_of(provider, configuration, "101")
    operation = provider.request_restore(configuration, server, first)

    for _ in range(MAX_POLLS):
        operation = provider.operation_state(configuration, operation)

    assert operation.state is OperationState.RUNNING


def test_a_rollback_with_an_instance_status_that_is_not_final_stays_running(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    first = made(provider, configuration)
    mock_contabo.state.interim_status = "installing"
    server = server_of(provider, configuration, "101")
    operation = provider.request_restore(configuration, server, first)

    seen = [provider.operation_state(configuration, operation) for _ in range(2)]

    assert [o.state for o in seen] == [OperationState.RUNNING] * 2
    assert follow(provider, configuration, operation).state is (
        OperationState.SUCCEEDED
    )


def test_a_snapshot_that_disappears_during_the_rollback_fails_the_operation(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    first = made(provider, configuration)
    server = server_of(provider, configuration, "101")
    operation = provider.request_restore(configuration, server, first)
    mock_contabo.state.snapshots.clear()
    mock_contabo.state.transitions.clear()

    final = provider.operation_state(configuration, operation)

    assert final.state is OperationState.FAILED
    assert "no longer listed" in (final.detail or "")


def test_an_instance_that_goes_to_error_during_the_rollback_fails_the_operation(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    first = made(provider, configuration)
    made(provider, configuration)
    server = server_of(provider, configuration, "101")
    mock_contabo.state.transition_fails = True

    final = follow(
        provider, configuration, provider.request_restore(configuration, server, first)
    )

    assert final.state is OperationState.FAILED
    assert "error" in (final.detail or "")


def test_an_undated_snapshot_stops_the_rollback_from_being_judged(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    first = made(provider, configuration)
    server = server_of(provider, configuration, "101")
    operation = provider.request_restore(configuration, server, first)
    mock_contabo.state.snapshots_without_dates = True

    with pytest.raises(ProviderError, match="no creation date"):
        for _ in range(MAX_POLLS):
            provider.operation_state(configuration, operation)


@pytest.mark.parametrize("status", [403, 429, 404])
def test_a_rejected_rollback_is_failed_and_deletes_nothing(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
    status: int,
) -> None:
    first = made(provider, configuration)
    made(provider, configuration)
    mock_contabo.state.rollback_forced_status = status
    server = server_of(provider, configuration, "101")

    operation = provider.request_restore(configuration, server, first)
    follow(provider, configuration, operation)

    assert operation.state is OperationState.FAILED
    assert str(status) in (operation.detail or "")
    assert len(provider.list_backups(configuration, server)) == 2


def test_a_server_error_on_the_rollback_is_unknown_because_it_may_have_started(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    first = made(provider, configuration)
    mock_contabo.state.rollback_forced_status = 500
    server = server_of(provider, configuration, "101")

    operation = provider.request_restore(configuration, server, first)

    assert operation.state is OperationState.UNKNOWN
    assert "may have reached the provider" in (operation.detail or "")


@pytest.mark.parametrize(
    ("status", "state"),
    [
        (401, OperationState.FAILED),
        (403, OperationState.FAILED),
        (429, OperationState.FAILED),
        (400, OperationState.FAILED),
        (500, OperationState.UNKNOWN),
        (503, OperationState.UNKNOWN),
    ],
)
def test_the_snapshot_request_answers_by_the_status_the_provider_gave(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
    status: int,
    state: OperationState,
) -> None:
    mock_contabo.state.snapshot_forced_status = status

    operation = back_up(provider, configuration)

    assert operation.state is state
    assert operation.detail
    assert not [v for v in CONTABO_VALUES.values() if v in operation.detail]
    assert mock_contabo.state.snapshot_requests == []


def test_a_token_that_expires_is_renewed_for_the_snapshot_request(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    server = server_of(provider, configuration, "101")
    mock_contabo.state.token_expires_after = 1

    operation = provider.request_backup(configuration, server, None)

    assert operation.state is OperationState.RUNNING
    assert mock_contabo.state.tokens_issued == 2


def test_the_backup_module_names_no_http_call_and_no_deletion() -> None:
    source = inspect.getsource(backup_module)

    for call in ("httpx", ".post(", ".put(", ".delete(", ".patch(", ".request("):
        assert call not in source
    for other in ("reinstall", "rescue", "resize", "upgrade", "DELETE"):
        assert other not in source
