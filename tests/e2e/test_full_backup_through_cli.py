"""Backing up and restoring a whole server end to end: real composition root,
both mock providers.

Each test runs for Proxmox VE (an archive on a storage) and for Contabo (a
snapshot). They show the acceptance criteria of US-001.11 (back up a whole
server) and US-001.12 (restore a whole server from a backup) and the Go criteria
of MIL-008: nothing is sent before the confirmation, a refused request sends and
records nothing, a request is never repeated, the write allow-list holds and no
secret is stored or shown. The provider that does not support the capability is a
fake without it, behind the same command line.
"""

import copy
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.backup_record import BackupRecord
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.infrastructure.configuration import (
    JsonLinesBackupStore,
    JsonLinesConfigurationStore,
)
from tests.contract.conftest import (
    CONTABO_ROLLBACK_WRITE,
    CONTABO_SNAPSHOT_WRITES,
    CONTABO_VALUES,
    CONTABO_WRONG_VALUES,
    TOKEN,
    WRONG_TOKEN,
)
from tests.e2e.conftest import MultiProviderSession, Result
from tests.unit.application.fakes import FakeBackupProvider
from tests.unit.application.samples import DB
from tests.unit.presentation.cli.harness import Harness

FAST = ("--poll-interval", "0.02", "--timeout", "10")
SHORT = ("--poll-interval", "0.05", "--timeout", "0.3")
PROVIDERS = ["proxmox", "contabo"]
NEVER = 10**9
SERVER = "101"
SEEDED_AT = datetime(2026, 1, 1, tzinfo=UTC)
BACKUP_LINE = re.compile(r"^Backup: (?P<id>\S+) \(alias (?P<alias>[0-9a-f]{8})\)", re.M)
ROLLBACK_ID = re.compile(r"/snapshots/[^/]+/rollback")
PROXMOX_DUMP = ("vzdump", "/api2/json/nodes/pve2/vzdump")
PROXMOX_RESTORE = ("restore", "/api2/json/nodes/pve2/lxc")
CONTABO_DUMP = ("POST", "/v1/compute/instances/101/snapshots")


@dataclass(slots=True)
class Target:
    """One provider behind the CLI, with the hooks its mock offers."""

    session: MultiProviderSession
    name: str
    kind: str
    backup_consequence: str
    restore_consequence: str
    blocked_text: str
    secret_values: tuple[str, ...]
    backup_write: tuple[str, str]
    restore_write: tuple[str, str]
    allowed_writes: frozenset[tuple[str, str]]
    writes: Callable[[], list[tuple[str, str]]]
    change_server: Callable[[], None]
    server_is_restored: Callable[[], bool]
    block_restore: Callable[[], None]
    reject_backup: Callable[[], None]
    reject_restore: Callable[[], None]
    fail_operations: Callable[[], None]
    lose_backup_answer: Callable[[], None]
    lose_restore_answer: Callable[[], None]
    use_wrong_credentials: Callable[[], None]
    seed: Callable[[str, int], str]
    drop_at_provider: Callable[[str], None]

    def full_backup(self, server: str = SERVER, *extra: str) -> Result:
        return self.session.run("server", "full-backup", self.name, server, *extra)

    def backed_up(self, server: str = SERVER, *extra: str) -> Result:
        """Back a server up with the confirmation given up front."""
        return self.full_backup(server, "--yes", *FAST, *extra)

    def full_restore(self, backup: str, *extra: str, server: str = SERVER) -> Result:
        return self.session.run(
            "server", "full-restore", self.name, server, backup, *extra
        )

    def restored(self, backup: str, *extra: str, server: str = SERVER) -> Result:
        """Restore with every confirmation given up front (an unmanaged server)."""
        return self.full_restore(
            backup, "--yes", "--confirm-unmanaged", server, *extra, server=server
        )

    def make_backup(self, server: str = SERVER) -> tuple[str, str]:
        """Back a server up through the CLI; return the backup id and its alias."""
        result = self.backed_up(server)
        assert result.code == 0, result.err
        return self.backup_of(result)

    def seeded(self, number: int = 0, server: str = SERVER) -> str:
        """Make the provider hold a backup, as if it had been made elsewhere.

        Restore tests start from one, so they do not pay for a backup each; the
        backup itself is shown by the US-001.11 tests.
        """
        return self.seed(server, number)

    def record(self, backup_id: str, number: int = 0) -> None:
        """Record a backup as this client would have once its operation ended."""
        [configuration] = self.file_rows("provider_configuration")
        JsonLinesBackupStore(self.session.configuration_file).save(
            BackupRecord(
                ConfigurationId(str(configuration["configuration_id"])),
                SERVER,
                backup_id,
                BackupKind(self.kind),
                f"seeded {backup_id}",
                SEEDED_AT + timedelta(hours=number),
                OperationId(f"seeded-{number}"),
            )
        )

    def backup_of(self, result: Result) -> tuple[str, str]:
        found = BACKUP_LINE.search(result.out)
        assert found is not None, result.out
        return found["id"], found["alias"]

    def manage(self, server: str = SERVER) -> None:
        """Record the server as managed, as a provisioning would have."""
        [configuration] = self.file_rows("provider_configuration")
        JsonLinesConfigurationStore(
            self.session.configuration_file
        ).save_managed_server(
            ManagedServerRecord(
                ConfigurationId(str(configuration["configuration_id"])),
                server,
                SEEDED_AT,
            )
        )

    def file_rows(self, record_type: str) -> list[dict[str, object]]:
        text = self.file_text()
        rows = [json.loads(line) for line in text.splitlines()]
        return [r for r in rows if r["record_type"] == record_type]

    def file_text(self) -> str:
        path = self.session.configuration_file
        return path.read_text("utf-8") if path.exists() else ""

    def operation_id(self, result: Result) -> str:
        for line in result.out.splitlines():
            if line.startswith("Operation:"):
                return line.split(":", 1)[1].strip()
        raise AssertionError(f"no operation in the output: {result.out!r}")

    def operations(self, action: str) -> list[dict[str, object]]:
        return [r for r in self.file_rows("operation") if r["action"] == action]

    def assert_no_secret(self, *texts: str) -> None:
        for text in texts:
            for value in self.secret_values:
                assert value not in text


def _proxmox_target(session: MultiProviderSession) -> Target:
    mock = session.proxmox
    added = session.run("provider", "add", "--address", mock.address, "--name", "Lab")
    assert added.code == 0, added.err

    def block_restore() -> None:
        """Server 100 runs; a restore over it is refused."""

    def seed(server: str, number: int) -> str:
        guest = next(g for g in mock.state.resources if str(g["vmid"]) == server)
        node, kind = str(guest["node"]), str(guest["type"])
        extension = "tar" if kind == "lxc" else "vma"
        stamp = f"2026_10_20-1{number}_00_00"
        volid = f"local:backup/vzdump-{kind}-{server}-{stamp}.{extension}"
        created = datetime(2026, 10, 20, 10 + number, tzinfo=UTC)
        mock.state.storage_content.setdefault(f"{node}/local", []).append(
            {
                "volid": volid,
                "content": "backup",
                "format": extension,
                "size": 1048576,
                "vmid": int(server),
                "notes": f"iac-node-forge seeded-{number} {guest['name']}",
                "protected": 0,
                "ctime": int(created.timestamp()),
            }
        )
        mock.state.archive_states[volid] = copy.deepcopy(guest)
        return volid

    def drop(volid: str) -> None:
        for key, items in mock.state.storage_content.items():
            mock.state.storage_content[key] = [i for i in items if i["volid"] != volid]

    return Target(
        session,
        "Lab",
        "archive",
        "writes a backup of guest 101",
        "is overwritten by the backup",
        "must be shut down first",
        (TOKEN, WRONG_TOKEN, TOKEN.split("=", 1)[1], WRONG_TOKEN.split("=", 1)[1]),
        PROXMOX_DUMP,
        PROXMOX_RESTORE,
        frozenset({PROXMOX_DUMP, PROXMOX_RESTORE}),
        lambda: mock.write_classes,
        lambda: mock.state.resources[1].update(name="changed"),
        lambda: mock.state.resources[1]["name"] == "db",
        block_restore,
        lambda: setattr(mock.state, "dump_forced_status", 403),
        lambda: setattr(mock.state, "restore_forced_status", 403),
        lambda: setattr(mock.state, "task_exitstatus", "job errors: guest is locked"),
        lambda: setattr(mock.state, "task_polls_before_end", NEVER),
        lambda: setattr(mock.state, "task_polls_before_end", NEVER),
        lambda: setattr(session, "secrets", {**session.secrets, "token": WRONG_TOKEN}),
        seed,
        drop,
    )


def _contabo_target(session: MultiProviderSession) -> Target:
    mock = session.contabo
    added = session.run(
        "provider",
        "add",
        "--kind",
        "contabo",
        "--address",
        mock.address,
        "--name",
        "Cloud",
    )
    assert added.code == 0, added.err

    def writes() -> list[tuple[str, str]]:
        return [
            (r.method, ROLLBACK_ID.sub("/snapshots/{snapshot}/rollback", r.path))
            for r in mock.write_requests
        ]

    def seed(server: str, number: int) -> str:
        created = datetime(2026, 10, 20, 10 + number, tzinfo=UTC)
        snapshot_id = f"seeded-{server}-{number}"
        mock.state.snapshots.append(
            {
                "snapshotId": snapshot_id,
                "name": f"iacnf-seeded{number}",
                "description": f"Created by iac-node-forge, operation seeded-{number}",
                "instanceId": int(server),
                "createdDate": _iso(created),
                "autoDeleteDate": _iso(created + timedelta(days=30)),
            }
        )
        return snapshot_id

    def drop(snapshot_id: str) -> None:
        mock.state.snapshots = [
            s for s in mock.state.snapshots if s["snapshotId"] != snapshot_id
        ]

    return Target(
        session,
        "Cloud",
        "snapshot",
        "creates a snapshot of instance 101",
        "rolled back to snapshot",
        "not available for a restore",
        tuple(CONTABO_VALUES.values()) + (CONTABO_WRONG_VALUES["api_password"],),
        CONTABO_DUMP,
        CONTABO_ROLLBACK_WRITE,
        CONTABO_SNAPSHOT_WRITES | {CONTABO_ROLLBACK_WRITE},
        writes,
        lambda: None,
        lambda: [i for i, _ in mock.state.rolled_back] == [SERVER],
        lambda: mock.state.instances[0].update(status="cancelled"),
        lambda: setattr(mock.state, "snapshot_forced_status", 403),
        lambda: setattr(mock.state, "rollback_forced_status", 403),
        lambda: setattr(mock.state, "transition_fails", True),
        lambda: setattr(mock.state, "snapshot_forced_status", 500),
        lambda: setattr(mock.state, "rollback_forced_status", 500),
        lambda: session.secrets.update(CONTABO_WRONG_VALUES),
        seed,
        drop,
    )


@pytest.fixture(params=PROVIDERS)
def target(
    request: pytest.FixtureRequest, multi_session: MultiProviderSession
) -> Iterator[Target]:
    if request.param == "proxmox":
        yield _proxmox_target(multi_session)
    else:
        yield _contabo_target(multi_session)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _alias(backup_id: str) -> str:
    return Backup(backup_id, BackupKind.ARCHIVE, "x", SEEDED_AT).alias()


def _line(result: Result, label: str) -> str:
    """Return the first output line that starts with ``label``."""
    return next(ln.rstrip() for ln in result.out.splitlines() if ln.startswith(label))


# --- US-001.11: back up a whole server ---------------------------------------


def test_a_backup_is_followed_to_completion_and_told_where_it_is_kept(
    target: Target,
) -> None:
    """US-001.11: given a provider that supports it, followed and told where."""
    result = target.backed_up()

    assert result.code == 0, result.err
    assert "About to back up server '101' (db)" in result.out
    assert _line(result, "Backup:").endswith(target.kind)
    assert target.backup_consequence in result.out
    assert "Requested: backup of server '101'" in result.out
    assert "succeeded" in result.out
    kept = _line(result, "Kept at:")
    assert target.writes() == [target.backup_write]
    [operation] = target.operations("backup")
    assert operation["state"] == "succeeded"
    assert operation["server_identifier"] == SERVER
    assert target.backup_consequence in str(operation["consequence"])
    [record] = target.file_rows("backup")
    assert record["kind"] == target.kind
    assert record["operation_id"] == operation["operation_id"]
    assert str(record["location"]) in kept
    assert str(record["backup_id"]) in result.out
    if target.kind == "snapshot":
        assert "not an independent backup" in result.out


def test_the_backup_is_listed_and_completing_again_records_no_second_one(
    target: Target,
) -> None:
    """US-001.11, OC-009: it can be read again; completing again changes nothing."""
    done = target.backed_up()
    backup_id, alias = target.backup_of(done)
    [operation] = target.operations("backup")
    before = target.file_text()

    listing = target.session.run("server", "full-backups", target.name, SERVER)
    again = target.session.run("operation", target.operation_id(done))

    assert listing.code == 0, listing.err
    heading = f"Backups of server '101' at provider '{target.name}' ({target.kind})"
    assert heading in listing.out
    assert alias in listing.out
    assert backup_id in listing.out
    assert "made by this client" in listing.out
    assert f"operation {operation['operation_id']}" in listing.out
    assert again.code == 0, again.err
    assert "Kept at:" in again.out
    assert target.file_text() == before
    assert target.writes() == [target.backup_write]


def test_a_request_can_be_followed_later_and_then_recorded(target: Target) -> None:
    """US-001.11: operation --follow ends the operation and records the backup."""
    started = target.backed_up(SERVER, "--no-wait")
    operation_id = target.operation_id(started)

    assert target.file_rows("backup") == []
    followed = target.session.run("operation", operation_id, "--follow", *FAST)

    assert started.code == 0, started.err
    assert followed.code == 0, followed.err
    assert "Kept at:" in followed.out
    [record] = target.file_rows("backup")
    assert record["operation_id"] == operation_id
    assert target.writes() == [target.backup_write]


def test_nothing_is_sent_before_the_plain_confirmation(target: Target) -> None:
    """US-001.11: the preview is shown first; a backup needs a yes."""
    session = target.session
    without_yes = target.full_backup()
    session.interactive = True
    session.answers[:] = ["no"]
    refused = target.full_backup(SERVER, *FAST)

    for result in (without_yes, refused):
        assert result.code == 1
        assert "About to back up server '101'" in result.out
        assert "Consequence:" in result.out
        assert target.backup_consequence in result.out
    assert "--yes" in without_yes.err
    assert "not confirmed" in refused.err
    assert "Do you want to back up this server?" in session.questions[-1]
    assert target.writes() == []
    assert target.file_rows("operation") == []


def test_a_server_that_does_not_exist_is_reported_without_a_request(
    target: Target,
) -> None:
    """OC-009 requestBackup: no such server, nothing created, nothing sent."""
    result = target.backed_up("999")

    assert result.code == 1
    assert "999" in result.err
    assert "No request was sent" in result.err
    assert target.writes() == []
    assert target.file_rows("operation") == []


def test_a_rejected_backup_is_recorded_as_failed_and_not_repeated(
    target: Target,
) -> None:
    """OC-009 requestBackup: a rejection fails the operation, records no backup."""
    target.reject_backup()

    result = target.backed_up()
    shown = target.session.run("operation", target.operation_id(result))

    assert result.code == 1
    assert "rejected the backup of server '101'" in result.err
    assert "not repeated" in result.err
    assert "failed" in shown.out
    assert "Consequence:" in shown.out
    assert target.writes() == [target.backup_write]
    [operation] = target.operations("backup")
    assert operation["state"] == "failed"
    assert target.backup_consequence in str(operation["consequence"])
    assert target.file_rows("backup") == []


def test_a_backup_that_fails_later_records_no_backup(target: Target) -> None:
    """OC-009 completeBackup: a failed operation records nothing and says why."""
    target.fail_operations()

    result = target.backed_up()

    assert result.code == 1
    assert "the backup of server '101' failed" in result.err
    assert "State left at the provider" in result.err
    assert "None" not in result.err
    assert target.writes() == [target.backup_write]
    [operation] = target.operations("backup")
    assert operation["state"] == "failed"
    assert target.file_rows("backup") == []


def test_a_lost_answer_is_unknown_and_the_backup_request_is_not_repeated(
    target: Target,
) -> None:
    """MIL-008 Go criterion: an unknown outcome is recorded and never repeated."""
    target.lose_backup_answer()

    result = target.session.run(
        "server", "full-backup", target.name, SERVER, "--yes", *SHORT
    )

    assert result.code == 1
    assert "unknown" in result.out
    assert "unknown" in result.err
    assert "None" not in result.err
    assert target.writes() == [target.backup_write]
    [operation] = target.operations("backup")
    assert operation["state"] == "unknown"
    assert target.backup_consequence in str(operation["consequence"])
    assert target.file_rows("backup") == []


@pytest.mark.parametrize(
    ("kind", "name"),
    [(ProviderKind.PROXMOX_VE, "Lab"), (ProviderKind.CONTABO, "Cloud")],
)
def test_a_provider_that_does_not_support_it_is_shown_as_unsupported(
    kind: ProviderKind, name: str
) -> None:
    """US-001.11, US-001.12: a provider without the capability is shown as such."""
    harness = Harness()
    fake = FakeBackupProvider(servers=(DB,), supported=False, clock=harness.clock)
    harness.provider = fake
    harness.configurations.save(
        ProviderConfiguration(
            ConfigurationId("cfg-1"), kind, name, "https://provider.example"
        )
    )

    capabilities = harness.run("capabilities", name)
    commands = [
        harness.run("server", "full-backup", name, "101", "--yes"),
        harness.run("server", "full-backups", name, "101"),
        harness.run("server", "full-restore", name, "101", "x", "--yes"),
    ]

    assert capabilities.code == 0, capabilities.err
    assert re.search(r"backup_server\s+unsupported", capabilities.out)
    assert re.search(r"restore_server\s+unsupported", capabilities.out)
    for result in commands:
        assert result.code == 1
        assert "does not support" in result.err
        assert "capabilities" in result.err
    assert fake.requests == []
    assert harness.operations.saved == []
    assert harness.backups.saved == []


def test_proxmox_asks_for_a_storage_when_several_accept_backups(
    multi_session: MultiProviderSession,
) -> None:
    """ADR-0010: several storages and none named are listed; the user chooses."""
    proxmox = _proxmox_target(multi_session)
    state = multi_session.proxmox.state
    state.storages["pve2"] = [
        {"storage": "local", "content": "iso,vztmpl,backup"},
        {"storage": "vault", "content": "backup"},
        {"storage": "local-lvm", "content": "rootdir,images"},
    ]

    refused = proxmox.backed_up()
    unsuitable = proxmox.backed_up(SERVER, "--storage", "local-lvm")
    chosen = proxmox.backed_up(SERVER, "--storage", "vault")

    assert refused.code == 1
    assert "local, vault" in refused.err
    assert "--storage" in refused.err
    assert unsuitable.code == 1
    assert "'local-lvm' cannot hold a backup" in unsuitable.err
    assert chosen.code == 0, chosen.err
    assert "Kept at: storage vault, volume vault:backup/" in chosen.out
    assert proxmox.writes() == [PROXMOX_DUMP]
    assert state.dumped[0]["storage"] == "vault"


def test_proxmox_refuses_a_backup_when_no_storage_accepts_backups(
    multi_session: MultiProviderSession,
) -> None:
    """ADR-0010: no storage with the content type backup; nothing is sent."""
    proxmox = _proxmox_target(multi_session)
    multi_session.proxmox.state.storages["pve2"] = [
        {"storage": "local", "content": "iso,vztmpl"}
    ]

    result = proxmox.backed_up()

    assert result.code == 1
    assert "no storage" in result.err
    assert "No request was sent" in result.err
    assert proxmox.writes() == []
    assert proxmox.file_rows("operation") == []


def test_contabo_has_no_storage_to_name(multi_session: MultiProviderSession) -> None:
    """ADR-0010: a storage named at Contabo is refused and nothing is sent."""
    contabo = _contabo_target(multi_session)

    result = contabo.backed_up(SERVER, "--storage", "local")

    assert result.code == 1
    assert "cannot hold a backup" in result.err
    assert contabo.writes() == []
    assert contabo.file_rows("operation") == []


# --- US-001.12: restore a whole server from a backup -------------------------


def test_the_restore_states_what_it_replaces_and_needs_the_typed_identifier(
    target: Target,
) -> None:
    """US-001.12: told what it will replace and must confirm; nothing sent before."""
    session = target.session
    target.manage()
    backup_id = target.seeded()

    without_yes = target.full_restore(backup_id)
    session.interactive = True
    session.answers[:] = [""]
    refused = target.full_restore(backup_id, *FAST)

    for result in (without_yes, refused):
        assert result.code == 1
        assert "About to restore server '101' (db)" in result.out
        assert _line(result, "Managed:").endswith("yes")
        assert backup_id in result.out
        assert target.restore_consequence in result.out
        assert "cannot be undone" in result.out
    assert "--yes" in without_yes.err
    assert "not confirmed" in refused.err
    assert "Type the server identifier '101' to restore" in session.questions[-1]
    assert target.writes() == []
    assert target.operations("restore") == []


def test_a_completed_restore_reports_and_records_the_state_of_the_server(
    target: Target,
) -> None:
    """US-001.12: when it ends, the state of the server is reported and recorded."""
    target.manage()
    backup_id = target.seeded()
    target.change_server()

    result = target.full_restore(backup_id, "--yes", *FAST)

    assert result.code == 0, result.err
    assert "Requested: restore of server '101'" in result.out
    assert "succeeded" in result.out
    assert re.search(r"State after the restore: server '101' is \w+\.", result.out)
    assert "Server state:" in result.out
    assert target.writes() == [target.restore_write]
    assert target.server_is_restored()
    [operation] = target.operations("restore")
    assert operation["state"] == "succeeded"
    assert str(operation["detail"]).startswith("server state after the restore: ")
    assert target.restore_consequence in str(operation["consequence"])
    assert str(operation["consequence"]) in result.out
    assert operation["provider_reference"] is not None

    before = target.file_text()
    again = target.session.run("operation", target.operation_id(result))

    assert again.code == 0, again.err
    assert "State after the restore" in again.out
    assert target.file_text() == before
    assert target.writes() == [target.restore_write]


def test_a_restore_can_be_followed_later_and_then_completed(target: Target) -> None:
    """US-001.12: operation --follow ends the restore and records the state."""
    target.manage()
    backup_id = target.seeded()
    started = target.full_restore(backup_id, "--yes", "--no-wait")
    operation_id = target.operation_id(started)
    [before] = target.operations("restore")

    followed = target.session.run("operation", operation_id, "--follow", *FAST)

    assert started.code == 0, started.err
    assert before["detail"] is None
    assert followed.code == 0, followed.err
    assert "State after the restore" in followed.out
    [after] = target.operations("restore")
    assert str(after["detail"]).startswith("server state after the restore: ")


def test_a_backup_can_be_named_by_its_short_alias(target: Target) -> None:
    """ADR-0010: the identifier or the unique alias names the backup."""
    target.manage()
    alias = _alias(target.seeded())

    result = target.full_restore(alias, "--yes", *FAST)

    assert result.code == 0, result.err
    assert target.writes() == [target.restore_write]


def test_an_unmanaged_server_needs_the_extra_confirmation(target: Target) -> None:
    """US-001.12: given a server not managed by this client, one more is required."""
    backup_id = target.seeded()

    without_extra = target.full_restore(backup_id, "--yes")
    wrong_extra = target.full_restore(backup_id, "--yes", "--confirm-unmanaged", "999")

    assert without_extra.code == 1
    assert "not managed by this client" in without_extra.out
    assert "--confirm-unmanaged 101" in without_extra.err
    assert wrong_extra.code == 1
    assert "does not match" in wrong_extra.err
    assert target.writes() == []
    assert target.operations("restore") == []

    given = target.restored(backup_id, *FAST)

    assert given.code == 0, given.err
    assert target.writes() == [target.restore_write]


def test_the_identifier_is_typed_twice_for_an_unmanaged_server_at_a_terminal(
    target: Target,
) -> None:
    """US-001.12: at a terminal the extra confirmation is a second typing."""
    session = target.session
    backup_id = target.seeded()
    session.interactive = True
    session.answers[:] = [SERVER, "wrong"]
    mismatch = target.full_restore(backup_id, *FAST)
    session.answers[:] = [SERVER, SERVER]

    done = target.full_restore(backup_id, *FAST)

    assert mismatch.code == 1
    assert "does not match" in mismatch.err
    assert done.code == 0, done.err
    assert len(session.questions) == 4
    assert "type its identifier again" in session.questions[-1]
    assert target.writes() == [target.restore_write]


def test_a_name_or_a_prefix_is_not_the_identifier(target: Target) -> None:
    """MIL-008 Go criterion: only the exact identifier confirms; nothing is sent."""
    session = target.session
    target.manage()
    backup_id = target.seeded()
    session.interactive = True

    outcomes = []
    for typed in ("db", "10"):
        session.answers[:] = [typed]
        outcomes.append(target.full_restore(backup_id, *FAST))

    for result in outcomes:
        assert result.code == 1
        assert "does not match" in result.err
    assert target.writes() == []
    assert target.operations("restore") == []


def test_a_restore_that_cannot_be_made_now_is_refused_without_a_request(
    target: Target,
) -> None:
    """US-001.12: a running Proxmox VE guest and a cancelled Contabo instance."""
    backup_id = target.seeded(server="100")
    target.block_restore()

    result = target.restored(backup_id, server="100")

    assert result.code == 1
    assert target.blocked_text in result.err
    assert "No request was sent" in result.err
    assert "Consequence:" not in result.out
    assert target.writes() == []
    assert target.operations("restore") == []


def test_a_backup_that_is_not_listed_is_refused_without_a_request(
    target: Target,
) -> None:
    """OC-010 requestRestore: no such backup, nothing created, nothing sent."""
    target.seeded()

    result = target.restored("not-a-backup-of-this-server", *FAST)

    assert result.code == 1
    assert "lists no backup" in result.err
    assert "full-backups" in result.err
    assert "No request was sent" in result.err
    assert target.writes() == []
    assert target.operations("restore") == []


def test_a_server_that_does_not_exist_cannot_be_restored(target: Target) -> None:
    """OC-010 requestRestore: no such server, nothing created, nothing sent."""
    result = target.restored("anything", server="999")

    assert result.code == 1
    assert "999" in result.err
    assert "No request was sent" in result.err
    assert target.writes() == []


def test_a_rejected_restore_is_recorded_as_failed_and_not_repeated(
    target: Target,
) -> None:
    """OC-010 requestRestore: a rejection fails the operation."""
    target.manage()
    backup_id = target.seeded()
    target.reject_restore()

    result = target.full_restore(backup_id, "--yes", *FAST)
    shown = target.session.run("operation", target.operation_id(result))

    assert result.code == 1
    assert "rejected the restore of server '101'" in result.err
    assert "not repeated" in result.err
    assert "failed" in shown.out
    assert "Consequence:" in shown.out
    assert target.writes() == [target.restore_write]
    [operation] = target.operations("restore")
    assert operation["state"] == "failed"
    assert target.restore_consequence in str(operation["consequence"])


def test_a_restore_that_fails_later_reports_the_state_left(target: Target) -> None:
    """OC-010 completeRestore: a restore that fails later says what is left."""
    target.manage()
    first = target.seeded(0)
    target.seeded(1)
    target.fail_operations()

    result = target.full_restore(first, "--yes", *FAST)

    assert result.code == 1
    assert "the restore of server '101' failed" in result.err
    assert "State left at the provider: server '101' is" in result.err
    assert "None" not in result.err
    [operation] = target.operations("restore")
    assert operation["state"] == "failed"
    assert target.writes() == [target.restore_write]


def test_a_lost_answer_is_unknown_and_the_restore_request_is_not_repeated(
    target: Target,
) -> None:
    """MIL-008 Go criterion: an unknown outcome is recorded and never repeated."""
    target.manage()
    backup_id = target.seeded()
    target.lose_restore_answer()

    result = target.full_restore(backup_id, "--yes", *SHORT)

    assert result.code == 1
    assert "unknown" in result.out
    assert "unknown" in result.err
    assert "None" not in result.err
    assert target.writes() == [target.restore_write]
    [operation] = target.operations("restore")
    assert operation["state"] == "unknown"
    assert target.restore_consequence in str(operation["consequence"])


def test_proxmox_states_that_the_guest_is_overwritten(
    multi_session: MultiProviderSession,
) -> None:
    """ADR-0010: the archive overwrites the guest, its disks and its configuration."""
    proxmox = _proxmox_target(multi_session)
    backup_id = proxmox.seeded()

    result = proxmox.full_restore(backup_id)

    assert "Guest 101 (db) on node pve2 is overwritten by the backup" in result.out
    assert "its disks and its configuration are replaced" in result.out
    assert "The restore also deletes" not in result.out
    assert "not an independent backup" not in result.out


def test_contabo_lists_the_newer_snapshots_the_rollback_deletes(
    multi_session: MultiProviderSession,
) -> None:
    """ADR-0010: a rollback deletes every newer snapshot; the preview names them."""
    contabo = _contabo_target(multi_session)
    first, second, third = (contabo.seeded(number) for number in range(3))

    preview = contabo.full_restore(first)
    done = contabo.restored(first, *FAST)
    listing = contabo.session.run("server", "full-backups", contabo.name, SERVER)

    assert preview.code == 1
    before, _, deleted = preview.out.partition("The restore also deletes")
    assert second in deleted
    assert third in deleted
    assert first not in deleted
    assert first in before
    assert "deletes these newer snapshots" in preview.out
    assert "not an independent backup" in preview.out
    assert done.code == 0, done.err
    assert first in listing.out
    assert second not in listing.out
    assert third not in listing.out
    assert contabo.writes() == [CONTABO_ROLLBACK_WRITE]
    assert [i for i, _ in multi_session.contabo.state.rolled_back] == [SERVER]


def test_a_backup_the_provider_no_longer_lists_is_reported_apart_and_kept(
    target: Target,
) -> None:
    """OC-010 listBackups: a recorded backup that is gone is shown, not deleted."""
    kept, gone = target.seeded(0), target.seeded(1)
    target.record(kept, 0)
    target.record(gone, 1)
    target.drop_at_provider(gone)

    listing = target.session.run("server", "full-backups", target.name, SERVER)

    assert listing.code == 0, listing.err
    listed, _, apart = listing.out.partition("Recorded by this client but no longer")
    assert kept in listed
    assert gone not in listed
    assert gone in apart
    assert kept not in apart
    assert len(target.file_rows("backup")) == 2
    assert target.writes() == []


# --- The write allow-list and secrets -----------------------------------------


def test_only_the_backup_and_the_restore_writes_reach_the_provider(
    target: Target,
) -> None:
    """MIL-008 Go criterion: the writes are exactly the expected, and allowed."""
    target.full_backup()
    target.backed_up("999")
    target.session.run("server", "full-backups", target.name, SERVER)
    refused = target.writes()

    backup_id, _ = target.make_backup()
    target.full_restore(backup_id, "--yes", "--confirm-unmanaged", "7")
    after_refusals = target.writes()
    done = target.restored(backup_id, *FAST)
    writes = target.writes()

    assert refused == []
    assert after_refusals == [target.backup_write]
    assert done.code == 0, done.err
    assert writes == [target.backup_write, target.restore_write]
    assert set(writes) <= target.allowed_writes


def test_no_credential_value_appears_in_a_record_a_file_or_an_error(
    target: Target,
) -> None:
    """MIL-008 Go criterion: no secret in any record, file, message or output."""
    target.manage()
    done = target.backed_up()
    backup_id, _ = target.backup_of(done)
    results = [done, target.full_restore(backup_id)]
    target.reject_restore()
    results.append(target.restored(backup_id, *FAST))
    target.reject_backup()
    results.append(target.backed_up())
    target.use_wrong_credentials()
    results.append(target.session.run("server", "full-backups", target.name, SERVER))

    file_text = target.file_text()

    for record_type in ("backup", "operation"):
        assert record_type in file_text
    target.assert_no_secret(file_text)
    for result in results:
        target.assert_no_secret(result.out, result.err)
        assert "Traceback" not in result.err
