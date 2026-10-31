"""``server full-backup|full-backups|full-restore`` and ``operation`` with fakes."""

import pytest

from iac_node_forge.application.errors import ProviderAuthenticationError
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.operation import OperationId, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.server import Server
from iac_node_forge.presentation.cli.commands.server_access import is_access_request
from iac_node_forge.presentation.cli.commands.server_backup import is_backup_request
from iac_node_forge.presentation.cli.commands.server_full_backup import (
    is_full_backup_request,
)
from iac_node_forge.presentation.cli.commands.server_full_backups import (
    is_full_backups_request,
)
from iac_node_forge.presentation.cli.commands.server_full_restore import (
    is_full_restore_request,
)
from iac_node_forge.presentation.cli.commands.server_power import is_power_request
from iac_node_forge.presentation.cli.commands.server_provision import (
    is_provision_request,
)
from iac_node_forge.presentation.cli.commands.server_remove import is_remove_request
from iac_node_forge.presentation.cli.commands.server_restore import is_restore_request
from tests.unit.application.fakes import FakeBackupProvider
from tests.unit.application.samples import DB, LAB, LAB_ID, WEB
from tests.unit.presentation.cli.harness import CliRun, Harness

FAST = ("--poll-interval", "1", "--timeout", "30")
CANCELLED = Server("300", "old", "cancelled", "1 vCPU", (), False)
OTHER_VERBS = (
    "backup",
    "restore",
    "access",
    "define",
    "provision",
    "definitions",
    "remove",
    "start",
)
FULL_MATCHERS = {
    "full-backup": is_full_backup_request,
    "full-backups": is_full_backups_request,
    "full-restore": is_full_restore_request,
}
OTHER_MATCHERS = (
    is_backup_request,
    is_restore_request,
    is_access_request,
    is_provision_request,
    is_remove_request,
    is_power_request,
)


def backing_up(
    *,
    kind: BackupKind = BackupKind.ARCHIVE,
    storages: tuple[str, ...] = ("local",),
    interactive: bool = False,
    answers: tuple[str, ...] = (),
) -> tuple[Harness, FakeBackupProvider]:
    """Lab with WEB (running), DB (stopped) and a cancelled server."""
    harness = Harness(interactive=interactive, answers=answers)
    fake = FakeBackupProvider(
        servers=(WEB, DB, CANCELLED), kind=kind, storages=storages, clock=harness.clock
    )
    harness.provider = fake
    harness.configurations.save(LAB)
    return harness, fake


def operation_id(result: CliRun) -> str:
    for line in result.out.splitlines():
        if line.startswith("Operation:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no operation in: {result.out!r}")


def backed_up(harness: Harness, *extra: str) -> CliRun:
    result = harness.run("server", "full-backup", "Lab", "101", "--yes", *FAST, *extra)
    assert result.code == 0, result.err
    return result


def only_backup_id(fake: FakeBackupProvider) -> str:
    [backup] = fake.list_backups(LAB, DB)
    return backup.backup_id


@pytest.mark.parametrize("verb", FULL_MATCHERS)
def test_each_full_verb_is_matched_by_its_own_matcher_only(verb: str) -> None:
    for other, matches in FULL_MATCHERS.items():
        assert matches(["server", verb, "Lab", "101"]) is (other == verb)
        assert matches(["server", verb, "--help"]) is (other == verb)
    assert not any(m(["server", verb, "Lab", "101"]) for m in OTHER_MATCHERS)


@pytest.mark.parametrize("verb", OTHER_VERBS)
def test_the_other_server_commands_do_not_route_to_the_full_commands(
    verb: str,
) -> None:
    for argv in (
        ["server", verb, "Lab", "101"],
        ["server", verb, "Lab", "101", "x"],
        ["server", verb, "--help"],
    ):
        assert not any(matches(argv) for matches in FULL_MATCHERS.values())


def test_the_other_server_commands_still_reach_their_own_matcher() -> None:
    assert is_backup_request(["server", "backup", "Lab", "101", "/etc", "--to", "d"])
    assert is_restore_request(["server", "restore", "Lab", "101", "f", "--to", "/d"])
    assert is_access_request(["server", "access", "Lab", "101", "--user", "u"])
    assert is_provision_request(["server", "provision", "Lab", "x"])
    assert is_remove_request(["server", "remove", "Lab", "101"])
    assert is_power_request(["server", "stop", "Lab", "101"])
    assert not is_full_backup_request(["server", "backup", "Lab", "101"])
    assert not is_full_backups_request(["server", "full-backup", "Lab", "101"])
    assert not is_full_backup_request(["server", "full-backups", "Lab", "101"])
    assert not is_full_restore_request(["servers", "full-restore", "Lab", "101", "x"])


def test_a_command_with_each_other_verb_runs_its_own_command() -> None:
    harness, fake = backing_up()

    removal = harness.run("server", "remove", "Lab", "101")
    stop = harness.run("server", "stop", "Lab", "101")
    backup = harness.run("server", "backup", "Lab", "101", "/etc")

    assert "remove_server" in removal.err
    assert "full-backup" not in removal.err
    assert "stop" in stop.err + stop.out
    assert backup.code == 2
    assert fake.requests == []


@pytest.mark.parametrize("verb", FULL_MATCHERS)
def test_a_configuration_named_like_a_full_verb_still_shows_a_server(
    verb: str,
) -> None:
    harness, _ = backing_up()
    harness.configurations.save(
        ProviderConfiguration(
            ConfigurationId("cfg-x"), LAB.provider_kind, verb, LAB.address
        )
    )

    result = harness.run("server", verb, "100")

    assert result.code == 0, result.err
    assert "web" in result.out


def test_a_missing_backup_id_is_a_usage_error() -> None:
    harness, fake = backing_up()

    result = harness.run("server", "full-restore", "Lab", "101")

    assert result.code == 2
    assert fake.requests == []


def test_full_backup_prints_the_preview_before_asking_and_sends_nothing() -> None:
    harness, fake = backing_up()

    result = harness.run("server", "full-backup", "Lab", "101")

    assert result.code == 1
    for text in (
        "About to back up server '101' (db) at provider 'Lab'",
        "Lab (Proxmox VE)",
        "stopped",
        "Backup:",
        "archive",
        "Goes to:",
        "storage local",
        "Consequence:",
    ):
        assert text in result.out
    assert "--yes" in result.err
    assert "No request was sent" in result.err
    assert fake.requests == []
    assert harness.operations.saved == []


def test_full_backup_with_yes_follows_the_operation_and_tells_where_it_is_kept() -> (
    None
):
    harness, fake = backing_up()

    result = harness.run("server", "full-backup", "Lab", "101", "--yes", *FAST)

    assert result.code == 0, result.err
    assert fake.requests == [("backup", "101")]
    assert "Requested: backup of server '101'" in result.out
    assert "succeeded" in result.out
    assert "Kept at: storage local" in result.out
    assert "recorded by this client" in result.out
    [record] = harness.backups.saved
    assert record.server_identifier == "101"
    [operation] = {o.operation_id: o for o in harness.operations.saved}.values()
    assert operation.action is PowerAction.BACKUP
    assert operation.consequence


def test_a_snapshot_is_said_not_to_be_an_independent_backup() -> None:
    harness, _ = backing_up(kind=BackupKind.SNAPSHOT, storages=())

    result = harness.run("server", "full-backup", "Lab", "101", "--yes", *FAST)

    assert result.code == 0, result.err
    assert result.out.count("not an independent backup") == 2


def test_full_backup_no_wait_prints_the_operation() -> None:
    harness, fake = backing_up()

    result = harness.run("server", "full-backup", "Lab", "101", "--yes", "--no-wait")

    assert result.code == 0, result.err
    assert "running" in result.out
    assert f"operation {operation_id(result)} --follow" in result.out
    assert fake.requests == [("backup", "101")]
    assert harness.backups.saved == []


def test_full_backup_asks_a_plain_yes_at_a_terminal() -> None:
    harness, fake = backing_up(interactive=True, answers=("yes",))

    result = harness.run("server", "full-backup", "Lab", "101", *FAST)

    assert result.code == 0, result.err
    assert harness.asked == ["Do you want to back up this server? [yes/no]: "]
    assert fake.requests == [("backup", "101")]


def test_full_backup_answered_no_at_a_terminal_sends_nothing() -> None:
    harness, fake = backing_up(interactive=True, answers=("no",))

    result = harness.run("server", "full-backup", "Lab", "101", *FAST)

    assert result.code == 1
    assert "not confirmed" in result.err
    assert fake.requests == []
    assert harness.operations.saved == []


def test_full_backup_with_several_storages_asks_for_one() -> None:
    harness, fake = backing_up(storages=("local", "backups"))

    refused = harness.run("server", "full-backup", "Lab", "101", "--yes")
    chosen = harness.run(
        "server", "full-backup", "Lab", "101", "--yes", "--storage", "backups", *FAST
    )

    assert refused.code == 1
    assert "local, backups" in refused.err
    assert "--storage" in refused.err
    assert chosen.code == 0, chosen.err
    assert "Kept at: storage backups" in chosen.out
    assert fake.requests == [("backup", "101")]


def test_full_backup_without_a_storage_that_accepts_backups_is_refused() -> None:
    harness, fake = backing_up()
    fake.remove_storages()

    result = harness.run("server", "full-backup", "Lab", "101", "--yes")

    assert result.code == 1
    assert "no storage" in result.err
    assert fake.requests == []


def test_full_backup_of_an_unsupported_provider_is_shown_as_unsupported() -> None:
    harness = Harness()
    fake = FakeBackupProvider(servers=(DB,), supported=False, clock=harness.clock)
    harness.provider = fake
    harness.configurations.save(LAB)

    result = harness.run("server", "full-backup", "Lab", "101", "--yes")

    assert result.code == 1
    assert "does not support" in result.err
    assert "capabilities" in result.err
    assert fake.requests == []
    assert harness.operations.saved == []


def test_full_backup_of_an_unknown_server_is_not_found() -> None:
    harness, fake = backing_up()

    result = harness.run("server", "full-backup", "Lab", "999", "--yes")

    assert result.code == 1
    assert "no server '999'" in result.err
    assert fake.requests == []


def test_a_rejected_backup_is_reported_failed_and_not_repeated() -> None:
    harness, fake = backing_up()
    fake.reject_with(ProviderAuthenticationError("the provider answered 403"))

    result = harness.run("server", "full-backup", "Lab", "101", "--yes", *FAST)

    assert result.code == 1
    assert "rejected the backup of server '101'" in result.err
    assert "not repeated" in result.err
    [operation] = harness.operations.saved
    assert operation.state is OperationState.FAILED
    assert harness.backups.saved == []


def test_a_backup_that_cannot_be_located_is_incomplete() -> None:
    harness, fake = backing_up()
    fake.hide_backups = True

    result = harness.run("server", "full-backup", "Lab", "101", "--yes", *FAST)

    assert result.code == 1
    assert "incomplete" in result.err
    assert "could not be located" in result.err
    assert harness.backups.saved == []


def test_a_failed_backup_operation_reports_the_state_left() -> None:
    harness, fake = backing_up()
    fake.make_operations_fail()

    result = harness.run("server", "full-backup", "Lab", "101", "--yes", *FAST)

    assert result.code == 1
    assert "the backup of server '101' failed" in result.err
    assert "State left at the provider" in result.err
    assert harness.backups.saved == []


def test_full_backups_lists_the_backups_and_those_that_are_gone() -> None:
    harness, fake = backing_up()
    backed_up(harness)
    result = harness.run("server", "full-backups", "Lab", "101")

    assert result.code == 0, result.err
    assert "Backups of server '101' at provider 'Lab' (archive):" in result.out
    assert only_backup_id(fake) in result.out
    assert "made by this client" in result.out
    assert "operation " in result.out
    assert "no longer listed" not in result.out


def test_full_backups_of_a_server_without_backups_says_so() -> None:
    harness, _ = backing_up()

    result = harness.run("server", "full-backups", "Lab", "101")

    assert result.code == 0, result.err
    assert "No backups are listed for server '101'" in result.out


def test_full_backups_of_an_unsupported_provider_is_an_error() -> None:
    harness = Harness()
    harness.provider = FakeBackupProvider(servers=(DB,), supported=False)
    harness.configurations.save(LAB)

    result = harness.run("server", "full-backups", "Lab", "101")

    assert result.code == 1
    assert "does not support" in result.err


def test_full_restore_prints_the_consequence_first_and_needs_the_confirmation() -> None:
    harness, fake = backing_up()
    backed_up(harness)
    backup_id = only_backup_id(fake)

    result = harness.run("server", "full-restore", "Lab", "101", backup_id)

    assert result.code == 1
    for text in (
        "About to restore server '101' (db) at provider 'Lab'",
        "Managed:",
        "Restore from:",
        backup_id,
        "cannot be undone",
    ):
        assert text in result.out
    assert "--yes" in result.err
    assert "--confirm-unmanaged 101" in result.err
    assert [r for r in fake.requests if r[0] == "restore"] == []


def test_full_restore_of_an_unmanaged_server_needs_the_second_confirmation() -> None:
    harness, fake = backing_up()
    backed_up(harness)
    backup_id = only_backup_id(fake)
    base = ("server", "full-restore", "Lab", "101", backup_id, "--yes")

    wrong = harness.run(*base, "--confirm-unmanaged", "102")
    right = harness.run(*base, "--confirm-unmanaged", "101", *FAST)

    assert wrong.code == 1
    assert "does not match" in wrong.err
    assert right.code == 0, right.err
    assert [r for r in fake.requests if r[0] == "restore"] == [("restore", "101")]


def test_full_restore_by_alias_follows_and_reports_the_state() -> None:
    harness, fake = backing_up()
    backed_up(harness)
    [backup] = fake.list_backups(LAB, DB)

    result = harness.run(
        "server",
        "full-restore",
        "Lab",
        "101",
        backup.alias(),
        "--yes",
        "--confirm-unmanaged",
        "101",
        *FAST,
    )

    assert result.code == 0, result.err
    assert "Requested: restore of server '101'" in result.out
    assert "Consequence:" in result.out
    assert "State after the restore: server '101' is stopped." in result.out
    assert "kept in the detail of the operation" in result.out
    restore = next(
        o
        for o in harness.operations.operations.values()
        if o.action is PowerAction.RESTORE
    )
    assert restore.state is OperationState.SUCCEEDED
    assert restore.detail == "server state after the restore: stopped"
    assert restore.consequence


def test_full_restore_types_the_identifier_twice_for_an_unmanaged_server() -> None:
    harness, fake = backing_up(interactive=True, answers=("101", "101"))
    backed_up(harness)
    backup_id = only_backup_id(fake)

    result = harness.run("server", "full-restore", "Lab", "101", backup_id, *FAST)

    assert result.code == 0, result.err
    assert "Type the server identifier '101' to restore the server" in harness.asked[0]
    assert "not managed by this client" in harness.asked[1]
    assert [r for r in fake.requests if r[0] == "restore"] == [("restore", "101")]


def test_full_restore_with_a_wrong_typed_identifier_sends_nothing() -> None:
    harness, fake = backing_up(interactive=True, answers=("102",))
    backed_up(harness)
    backup_id = only_backup_id(fake)

    result = harness.run("server", "full-restore", "Lab", "101", backup_id, *FAST)

    assert result.code == 1
    assert "does not match" in result.err
    assert "type '101' exactly" in result.err
    assert [r for r in fake.requests if r[0] == "restore"] == []


def test_full_restore_of_a_running_guest_is_refused() -> None:
    harness, fake = backing_up()
    harness.run("server", "full-backup", "Lab", "100", "--yes", *FAST)
    [backup] = fake.list_backups(LAB, WEB)

    result = harness.run(
        "server", "full-restore", "Lab", "100", backup.backup_id, "--yes"
    )

    assert result.code == 1
    assert "must be shut down first" in result.err
    assert f"server shutdown {LAB_ID} 100" in result.err
    assert [r for r in fake.requests if r[0] == "restore"] == []


def test_full_restore_of_a_backup_that_is_not_listed_is_refused() -> None:
    harness, fake = backing_up()

    result = harness.run("server", "full-restore", "Lab", "101", "nothing", "--yes")

    assert result.code == 1
    assert f"full-backups {LAB_ID} 101" in result.err
    assert fake.requests == []


def test_a_snapshot_restore_preview_lists_the_snapshots_it_deletes() -> None:
    harness, fake = backing_up(kind=BackupKind.SNAPSHOT, storages=())
    backed_up(harness)
    harness.clock.sleep(3600)
    backed_up(harness)
    first, second = fake.list_backups(LAB, DB)

    result = harness.run("server", "full-restore", "Lab", "101", first.backup_id)

    assert result.code == 1
    assert "The restore also deletes these backups:" in result.out
    assert second.backup_id in result.out
    assert "not an independent backup" in result.out


def test_a_rejected_restore_is_reported_and_not_repeated() -> None:
    harness, fake = backing_up()
    backed_up(harness)
    backup_id = only_backup_id(fake)
    fake.reject_with(ProviderAuthenticationError("the provider answered 403"))

    result = harness.run(
        "server",
        "full-restore",
        "Lab",
        "101",
        backup_id,
        "--yes",
        "--confirm-unmanaged",
        "101",
        *FAST,
    )

    assert result.code == 1
    assert "rejected the restore of server '101'" in result.err
    assert "not repeated" in result.err
    assert "State left at the provider: server is stopped" in result.err


def test_a_lost_answer_is_reported_unknown_and_not_repeated() -> None:
    harness, fake = backing_up()
    backed_up(harness)
    backup_id = only_backup_id(fake)
    fake.answer_unknown()

    result = harness.run(
        "server",
        "full-restore",
        "Lab",
        "101",
        backup_id,
        "--yes",
        "--confirm-unmanaged",
        "101",
        *FAST,
    )

    assert result.code == 1
    assert "outcome of the restore of server '101' is unknown" in result.err
    assert [r for r in fake.requests if r[0] == "restore"] == [("restore", "101")]


def test_operation_completes_a_finished_backup_and_a_restore() -> None:
    harness, fake = backing_up()
    started = harness.run("server", "full-backup", "Lab", "101", "--yes", "--no-wait")
    backup_operation = operation_id(started)
    still_running = harness.run("operation", backup_operation)
    harness.run("operation", backup_operation, "--follow", *FAST)
    shown = harness.run("operation", backup_operation)

    assert "recorded by this client" not in still_running.out
    assert shown.code == 0, shown.err
    assert "Kept at: storage local" in shown.out
    assert len(harness.backups.saved) == 1

    backup_id = only_backup_id(fake)
    restored = harness.run(
        "server",
        "full-restore",
        "Lab",
        "101",
        backup_id,
        "--yes",
        "--confirm-unmanaged",
        "101",
        "--no-wait",
    )
    restore_operation = operation_id(restored)
    harness.run("operation", restore_operation, "--follow", *FAST)
    again = harness.run("operation", restore_operation)

    assert again.code == 0, again.err
    assert "State after the restore: server '101' is stopped." in again.out
    ended = harness.operations.load(OperationId(restore_operation))
    assert ended.detail == "server state after the restore: stopped"


def test_the_help_names_the_three_commands() -> None:
    result = Harness().run("--help")

    text = result.out + result.err
    for name in ("full-backup", "full-backups", "full-restore"):
        assert name in text
