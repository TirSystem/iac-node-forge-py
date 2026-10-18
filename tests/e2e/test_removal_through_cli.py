"""Removing a server end to end: real composition root, both mock providers.

Each test runs for Proxmox VE and for Contabo. They show the acceptance
criteria of US-001.08 (delete or cancel a server) and the Go criteria of
MIL-006: nothing is sent before the typed confirmation, a refused removal sends
and records nothing, the write allow-list holds and no secret is stored or
shown. A managed server is seeded in the real store, because the removal is
what is under test, not the provisioning that made the record.
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.infrastructure.configuration import JsonLinesConfigurationStore
from tests.contract.conftest import (
    CONTABO_ALLOWED_WRITES,
    CONTABO_VALUES,
    CONTABO_WRONG_VALUES,
    PROXMOX_ALLOWED_WRITES,
    TOKEN,
    WRONG_TOKEN,
)
from tests.e2e.conftest import MultiProviderSession, Result

FAST = ("--poll-interval", "0.02", "--timeout", "10")
SHORT = ("--poll-interval", "0.05", "--timeout", "0.3")
PROVIDERS = ["proxmox", "contabo"]
NEVER = 10**9
SERVER = "101"
SEEDED_AT = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(slots=True)
class Target:
    """One provider behind the CLI, with the hooks its mock offers."""

    session: MultiProviderSession
    name: str
    consequence_text: str
    removal_kind: str
    blocked_id: str
    blocked_text: str
    secret_values: tuple[str, ...]
    allowed_writes: frozenset[tuple[str, str]]
    removal_write: tuple[str, str]
    writes: Callable[[], list[tuple[str, str]]]
    state_of: Callable[[str], str | None]
    block: Callable[[], None]
    reject_requests: Callable[[], None]
    fail_operations: Callable[[], None]
    never_finish: Callable[[], None]
    lose_answer: Callable[[], None]
    use_wrong_credentials: Callable[[], None]

    def remove(self, server: str, *extra: str) -> Result:
        return self.session.run("server", "remove", self.name, server, *extra)

    def confirmed(self, *extra: str, server: str = SERVER) -> Result:
        """Remove a managed server with every confirmation given up front."""
        return self.remove(server, "--yes", *extra)

    def confirmed_unmanaged(self, *extra: str, server: str = SERVER) -> Result:
        return self.remove(server, "--yes", "--confirm-unmanaged", server, *extra)

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
        text = self.session.configuration_file.read_text("utf-8")
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

    def assert_nothing_sent_or_recorded(self) -> None:
        assert self.writes() == []
        assert self.file_rows("operation") == []

    def assert_no_secret(self, *texts: str) -> None:
        for text in texts:
            for value in self.secret_values:
                assert value not in text


def _proxmox_target(session: MultiProviderSession) -> Target:
    mock = session.proxmox
    added = session.run("provider", "add", "--address", mock.address, "--name", "Lab")
    assert added.code == 0, added.err

    def state_of(identifier: str) -> str | None:
        return next(
            (
                str(g["status"])
                for g in mock.state.resources
                if str(g["vmid"]) == identifier
            ),
            None,
        )

    def use_wrong() -> None:
        session.secrets["token"] = WRONG_TOKEN

    return Target(
        session,
        "Lab",
        "destroys server 101 now",
        "destroy",
        "100",
        "must be shut down first",
        (TOKEN, WRONG_TOKEN, TOKEN.split("=", 1)[1], WRONG_TOKEN.split("=", 1)[1]),
        PROXMOX_ALLOWED_WRITES,
        ("DELETE", "/api2/json/nodes/pve2/lxc/101"),
        lambda: [(r.method, r.path) for r in mock.write_requests],
        state_of,
        lambda: None,
        lambda: setattr(mock.state, "delete_forced_status", 403),
        lambda: setattr(
            mock.state, "task_exitstatus", "unable to destroy CT 101 - busy"
        ),
        lambda: setattr(mock.state, "task_polls_before_end", NEVER),
        lambda: setattr(mock.state, "task_polls_before_end", NEVER),
        use_wrong,
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

    def state_of(identifier: str) -> str | None:
        return next(
            (
                str(i["status"])
                for i in mock.state.instances
                if str(i["instanceId"]) == identifier
            ),
            None,
        )

    return Target(
        session,
        "Cloud",
        "requests the cancellation of instance 101",
        "cancel",
        "100",
        "already removed",
        tuple(CONTABO_VALUES.values()) + (CONTABO_WRONG_VALUES["api_password"],),
        CONTABO_ALLOWED_WRITES,
        ("POST", "/v1/compute/instances/101/cancel"),
        lambda: [(r.method, r.path) for r in mock.write_requests],
        state_of,
        lambda: mock.state.instances[0].update(status="cancelled"),
        lambda: setattr(mock.state, "cancel_forced_status", 403),
        lambda: setattr(mock.state, "transition_fails", True),
        lambda: setattr(mock.state, "transition_reads", NEVER),
        lambda: setattr(mock.state, "cancel_forced_status", 500),
        lambda: session.secrets.update(CONTABO_WRONG_VALUES),
    )


@pytest.fixture(params=PROVIDERS)
def target(
    request: pytest.FixtureRequest, multi_session: MultiProviderSession
) -> Iterator[Target]:
    if request.param == "proxmox":
        yield _proxmox_target(multi_session)
    else:
        yield _contabo_target(multi_session)


def _line(result: Result, label: str) -> str:
    """Return the first output line that starts with ``label``."""
    return next(ln.rstrip() for ln in result.out.splitlines() if ln.startswith(label))


def test_the_consequence_is_stated_and_a_confirmation_is_required_before_sending(
    target: Target,
) -> None:
    """US-001.08: told what removal does at the provider, and must confirm."""
    session = target.session
    target.manage()

    without_yes = target.remove(SERVER)
    session.interactive = True
    session.answers[:] = [""]
    refused = target.remove(SERVER, *FAST)

    for result in (without_yes, refused):
        assert result.code == 1
        assert "About to remove server '101'" in result.out
        assert "Provider:" in result.out
        assert "State:" in result.out
        assert _line(result, "Managed:").endswith("yes")
        assert _line(result, "Removal:").endswith(target.removal_kind)
        assert target.consequence_text in result.out
    assert "--yes" in without_yes.err
    assert "not confirmed" in refused.err
    assert "Type the server identifier '101'" in session.questions[-1]
    target.assert_nothing_sent_or_recorded()
    assert target.state_of(SERVER) is not None


def test_an_unmanaged_server_needs_the_extra_confirmation(target: Target) -> None:
    """US-001.08: given a server not managed by this client, one more is required."""
    without_extra = target.confirmed()
    wrong_extra = target.remove(SERVER, "--yes", "--confirm-unmanaged", "999")

    assert without_extra.code == 1
    assert "not managed by this client" in without_extra.out
    assert "--confirm-unmanaged 101" in without_extra.err
    assert wrong_extra.code == 1
    assert "does not match" in wrong_extra.err
    target.assert_nothing_sent_or_recorded()

    given = target.confirmed_unmanaged(*FAST)

    assert given.code == 0, given.err
    assert target.writes() == [target.removal_write]
    assert "Managed: server '101' was not managed by this client" in given.out
    [operation] = target.file_rows("operation")
    assert (operation["action"], operation["state"]) == ("remove", "succeeded")
    assert target.file_rows("managed_server") == []


def test_the_identifier_is_typed_twice_for_an_unmanaged_server_at_a_terminal(
    target: Target,
) -> None:
    """US-001.08: at a terminal the extra confirmation is a second typing."""
    session = target.session
    session.interactive = True
    session.answers[:] = [SERVER, "wrong"]
    mismatch = target.remove(SERVER, *FAST)
    session.answers[:] = [SERVER, SERVER]

    done = target.remove(SERVER, *FAST)

    assert mismatch.code == 1
    assert "does not match" in mismatch.err
    assert done.code == 0, done.err
    assert len(session.questions) == 4
    assert "type its identifier again" in session.questions[-1]
    assert target.writes() == [target.removal_write]


def test_a_name_or_a_prefix_is_not_the_identifier(target: Target) -> None:
    """MIL-006 Go criterion: only the exact identifier confirms; nothing is sent."""
    session = target.session
    target.manage()
    session.interactive = True

    outcomes = []
    for typed in ("db", "10", "x101"):
        session.answers[:] = [typed]
        outcomes.append(target.remove(SERVER, *FAST))

    for result in outcomes:
        assert result.code == 1
        assert "does not match" in result.err
    target.assert_nothing_sent_or_recorded()
    assert target.state_of(SERVER) is not None


def test_a_completed_removal_records_the_consequence_and_ends_the_managed_record(
    target: Target,
) -> None:
    """US-001.08: when the removal ends, the consequence is recorded with it."""
    target.manage()
    shown = target.remove(SERVER)
    done = target.confirmed(*FAST)

    assert shown.code == 1
    assert done.code == 0, done.err
    assert "succeeded" in done.out
    assert "Consequence:" in done.out
    assert "the record of server '101' is ended" in done.out
    if target.removal_kind == "cancel":
        assert "effective date that Contabo reports" in done.out
        assert "cancelled, effective 2026-11-18" in done.out
    else:
        assert "cannot be undone" in done.out
    assert target.writes() == [target.removal_write]
    [operation] = target.file_rows("operation")
    assert (operation["action"], operation["state"]) == ("remove", "succeeded")
    assert operation["server_identifier"] == SERVER
    assert target.consequence_text in str(operation["consequence"])
    assert str(operation["consequence"]) in done.out
    [managed] = target.file_rows("managed_server")
    assert managed["ended_at"] is not None
    assert managed["ended_by_operation_id"] == operation["operation_id"]
    listing = target.session.run("servers")
    assert listing.code == 0, listing.err
    managed_lines = [
        ln.rstrip()
        for ln in listing.out.splitlines()
        if ln.rstrip().endswith("managed") and not ln.rstrip().endswith("not managed")
    ]
    assert managed_lines == []


def test_completing_a_removal_again_changes_nothing(target: Target) -> None:
    """OC-006 completeRemoval: a second completion ends no more records."""
    target.manage()
    done = target.confirmed(*FAST)
    before = target.file_text()

    again = target.session.run("operation", target.operation_id(done))

    assert again.code == 0, again.err
    assert "Consequence:" in again.out
    assert target.file_text() == before
    assert target.writes() == [target.removal_write]


def test_a_request_can_be_followed_later_and_then_completed(target: Target) -> None:
    """US-001.08: operation --follow ends the operation and the managed record."""
    target.manage()
    started = target.confirmed("--no-wait")
    operation_id = target.operation_id(started)
    [before] = target.file_rows("managed_server")

    followed = target.session.run("operation", operation_id, "--follow", *FAST)

    assert started.code == 0, started.err
    assert before["ended_at"] is None
    assert followed.code == 0, followed.err
    assert "is ended" in followed.out
    [after] = target.file_rows("managed_server")
    assert after["ended_by_operation_id"] == operation_id
    assert target.writes() == [target.removal_write]


def test_a_server_that_cannot_be_removed_now_is_refused_without_a_request(
    target: Target,
) -> None:
    """US-001.08: a running Proxmox VE server and a cancelled Contabo instance."""
    target.manage(target.blocked_id)
    target.block()

    result = target.confirmed(server=target.blocked_id)

    assert result.code == 1
    assert target.blocked_text in result.err
    assert "No request was sent" in result.err
    assert "Consequence:" not in result.out
    target.assert_nothing_sent_or_recorded()
    [managed] = target.file_rows("managed_server")
    assert managed["ended_at"] is None


def test_a_server_that_does_not_exist_is_reported_without_a_request(
    target: Target,
) -> None:
    """OC-006 requestRemoval: no such server, nothing created, nothing sent."""
    result = target.confirmed(server="999")

    assert result.code == 1
    assert "999" in result.err
    assert "No request was sent" in result.err
    target.assert_nothing_sent_or_recorded()


def test_a_rejected_removal_is_recorded_as_failed_and_not_repeated(
    target: Target,
) -> None:
    """OC-006 requestRemoval: a rejection fails the operation, keeps the record."""
    target.manage()
    target.reject_requests()

    result = target.confirmed(*FAST)
    shown = target.session.run("operation", target.operation_id(result))

    assert result.code == 1
    assert "rejected the removal" in result.err
    assert "failed" in shown.out
    assert "Consequence:" in shown.out
    assert target.writes() == [target.removal_write]
    [operation] = target.file_rows("operation")
    assert operation["state"] == "failed"
    assert target.consequence_text in str(operation["consequence"])
    [managed] = target.file_rows("managed_server")
    assert managed["ended_at"] is None
    assert target.state_of(SERVER) is not None


def test_a_failed_operation_reports_the_state_left_and_keeps_the_record(
    target: Target,
) -> None:
    """OC-006 completeRemoval: a removal that fails later ends no managed record."""
    target.manage()
    target.fail_operations()

    result = target.confirmed(*FAST)

    assert result.code == 1
    assert "failed" in result.err
    assert "State left at the provider: server '101' exists" in result.err
    assert "None" not in result.err
    [operation] = target.file_rows("operation")
    assert operation["state"] == "failed"
    [managed] = target.file_rows("managed_server")
    assert managed["ended_at"] is None
    assert target.writes() == [target.removal_write]


def test_a_lost_answer_is_unknown_and_the_request_is_not_repeated(
    target: Target,
) -> None:
    """MIL-006 Go criterion: an unknown outcome is recorded and never repeated."""
    target.manage()
    target.lose_answer()

    result = target.confirmed(*SHORT)

    assert result.code == 1
    assert "unknown" in result.out
    assert "unknown" in result.err
    assert "None" not in result.err
    assert target.writes() == [target.removal_write]
    [operation] = target.file_rows("operation")
    assert operation["state"] == "unknown"
    assert target.consequence_text in str(operation["consequence"])
    [managed] = target.file_rows("managed_server")
    assert managed["ended_at"] is None


def test_only_the_removal_write_reaches_the_provider(target: Target) -> None:
    """MIL-004 Go criterion 5, extended: the removal joins the write allow-list."""
    target.remove(SERVER)
    target.remove(SERVER, "--yes", "--confirm-unmanaged", "7")
    target.manage()
    target.confirmed(server="999")
    target.block()
    target.confirmed(server=target.blocked_id)
    target.session.run("servers")
    refused = target.writes()

    done = target.confirmed(*FAST)
    writes = target.writes()

    assert refused == []
    assert done.code == 0, done.err
    assert writes == [target.removal_write]
    assert set(writes) <= target.allowed_writes


def test_no_credential_value_appears_in_a_record_a_file_or_an_error(
    target: Target,
) -> None:
    """MIL-006 Go criterion: no secret in any record, file, message or output."""
    target.manage()
    results = [
        target.remove(SERVER),
        target.confirmed(server="999"),
        target.confirmed(*FAST),
    ]
    target.reject_requests()
    results.append(target.confirmed_unmanaged(*FAST, server=target.blocked_id))
    target.use_wrong_credentials()
    results.append(target.confirmed(*FAST))
    results.append(target.session.run("servers"))

    file_text = target.file_text()

    for record_type in ("managed_server", "operation"):
        assert record_type in file_text
    target.assert_no_secret(file_text)
    for result in results:
        target.assert_no_secret(result.out, result.err)
        assert "Traceback" not in result.err
