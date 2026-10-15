"""The power commands end to end: real composition root, both in-process mocks.

Each test runs for Proxmox VE and for Contabo. They show the acceptance
criteria of US-001.05 (track an operation) and US-001.07 (start, stop and
restart a server) and the Go criteria 3, 4, 5 and 6 of MIL-004.
"""

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from iac_node_forge.domain.power_action import PowerAction
from tests.contract.conftest import (
    CONTABO_ALLOWED_WRITES,
    CONTABO_VALUES,
    CONTABO_WRONG_VALUES,
    PROXMOX_ALLOWED_WRITES,
    PROXMOX_PLACES,
    PROXMOX_VERBS,
    TOKEN,
    WRONG_TOKEN,
)
from tests.e2e.conftest import MultiProviderSession, Result

FAST = ("--poll-interval", "0.02", "--timeout", "10")
PROVIDERS = ["proxmox", "contabo"]
NEVER = 10**9


@dataclass(slots=True)
class Target:
    """One provider behind the CLI, with the hooks its mock offers."""

    session: MultiProviderSession
    name: str
    secret_values: tuple[str, ...]
    allowed_writes: frozenset[tuple[str, str]]
    expected_write: Callable[[str, PowerAction], tuple[str, str]]
    writes: Callable[[], list[tuple[str, str]]]
    state_of: Callable[[str], str]
    reject_requests: Callable[[], None]
    fail_operations: Callable[[], None]
    never_finish: Callable[[], None]
    use_wrong_credentials: Callable[[], None]

    def power(self, verb: str, server: str, *extra: str) -> Result:
        return self.session.run("server", verb, self.name, server, *extra)

    def confirmed(self, verb: str, server: str, *extra: str) -> Result:
        """Run a power command with every confirmation given up front.

        No server is recorded as managed in this slice, so the identifier is
        always the extra confirmation.
        """
        return self.power(verb, server, "--yes", "--confirm-unmanaged", server, *extra)

    def operation_id(self, result: Result) -> str:
        for line in result.out.splitlines():
            if line.startswith("Operation:"):
                return line.split(":", 1)[1].strip()
        raise AssertionError(f"no operation in the output: {result.out!r}")

    def assert_no_secret(self, *texts: str) -> None:
        for text in texts:
            for value in self.secret_values:
                assert value not in text


def output_state(text: str) -> str:
    """Return the value of the ``State:`` line of an operation block."""
    for line in text.splitlines():
        if line.startswith("State:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no State line in: {text!r}")


def _proxmox_target(session: MultiProviderSession) -> Target:
    mock = session.proxmox
    result = session.run("provider", "add", "--address", mock.address, "--name", "Lab")
    assert result.code == 0, result.err

    def state_of(server: str) -> str:
        return next(
            str(item["status"])
            for item in mock.state.resources
            if str(item["vmid"]) == server
        )

    def use_wrong() -> None:
        session.secrets["token"] = WRONG_TOKEN

    return Target(
        session,
        "Lab",
        (TOKEN, WRONG_TOKEN, TOKEN.split("=", 1)[1], WRONG_TOKEN.split("=", 1)[1]),
        PROXMOX_ALLOWED_WRITES,
        lambda server, action: (
            "POST",
            f"/api2/json/nodes/{PROXMOX_PLACES[server][0]}/"
            f"{PROXMOX_PLACES[server][1]}/{server}/status/{PROXMOX_VERBS[action]}",
        ),
        lambda: [(r.method, r.path) for r in mock.write_requests],
        state_of,
        lambda: setattr(mock.state, "power_forced_status", 403),
        lambda: setattr(mock.state, "task_exitstatus", "command 'qm stop' failed"),
        lambda: setattr(mock.state, "task_polls_before_end", NEVER),
        use_wrong,
    )


def _contabo_target(session: MultiProviderSession) -> Target:
    mock = session.contabo
    result = session.run(
        "provider",
        "add",
        "--kind",
        "contabo",
        "--address",
        mock.address,
        "--name",
        "Cloud",
    )
    assert result.code == 0, result.err

    def state_of(server: str) -> str:
        return next(
            str(item["status"])
            for item in mock.state.instances
            if str(item["instanceId"]) == server
        )

    return Target(
        session,
        "Cloud",
        tuple(CONTABO_VALUES.values()) + (CONTABO_WRONG_VALUES["api_password"],),
        CONTABO_ALLOWED_WRITES,
        lambda server, action: (
            "POST",
            f"/v1/compute/instances/{server}/actions/{action.value}",
        ),
        lambda: [(r.method, r.path) for r in mock.write_requests],
        state_of,
        lambda: setattr(mock.state, "power_forced_status", 403),
        lambda: setattr(mock.state, "transition_fails", True),
        lambda: setattr(mock.state, "transition_reads", NEVER),
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


def test_a_running_operation_has_its_status_available(target: Target) -> None:
    """US-001.05: given a submitted operation, when running, its status shows."""
    started = target.confirmed("stop", "100", "--no-wait")
    operation_id = target.operation_id(started)

    shown = target.session.run("operation", operation_id)

    assert started.code == 0, started.err
    assert output_state(started.out) == "running"
    assert shown.code == 0, shown.err
    assert output_state(shown.out) == "running"
    assert operation_id in shown.out


def test_an_ended_operation_shows_and_records_its_outcome(target: Target) -> None:
    """US-001.05: given an operation that ended, outcome is shown and recorded."""
    done = target.confirmed("stop", "100", *FAST)
    operation_id = target.operation_id(done)

    shown = target.session.run("operation", operation_id)

    assert done.code == 0, done.err
    assert output_state(done.out) == "succeeded"
    assert output_state(shown.out) == "succeeded"
    assert "Finished:" in shown.out
    records = [
        json.loads(line)
        for line in target.session.configuration_file.read_text("utf-8").splitlines()
    ]
    operation = next(r for r in records if r["record_type"] == "operation")
    assert operation["operation_id"] == operation_id
    assert operation["state"] == "succeeded"
    assert operation["finished_at"] is not None


def test_an_ended_operation_shows_and_records_its_cause(target: Target) -> None:
    """US-001.05: a failed operation shows its cause and the cause is recorded."""
    target.fail_operations()

    done = target.confirmed("stop", "100", *FAST)
    shown = target.session.run("operation", target.operation_id(done))

    assert done.code == 1
    assert output_state(done.out) == "failed"
    assert shown.code == 0
    assert output_state(shown.out) == "failed"
    detail = next(line for line in shown.out.splitlines() if line.startswith("Detail:"))
    assert detail.split(":", 1)[1].strip()
    assert "failed" in done.err


def test_the_resulting_state_is_reported_for_a_supported_action(
    target: Target,
) -> None:
    """US-001.07: given a server and a supported action, the state is reported."""
    result = target.confirmed("stop", "100", *FAST)

    assert result.code == 0, result.err
    assert "succeeded" in result.out
    assert "Server state:  stopped" in result.out
    assert target.state_of("100") == "stopped"


def test_start_reports_the_resulting_state(target: Target) -> None:
    """US-001.07: start of a stopped server reports it as running."""
    result = target.power("start", "101", "--yes", "--confirm-unmanaged", "101", *FAST)

    assert result.code == 0, result.err
    assert "Server state:  running" in result.out
    assert target.state_of("101") == "running"


def test_a_forced_action_needs_the_confirmation_first(target: Target) -> None:
    """US-001.07: a forced action is not performed without its confirmation."""
    refused_by_default = target.power("stop", "100")
    target.session.interactive = True
    target.session.answers[:] = ["no"]
    refused_by_user = target.power("stop", "100", *FAST)

    assert refused_by_default.code == 1
    assert "--yes" in refused_by_default.err
    assert refused_by_user.code == 1
    assert "About to stop server '100'" in refused_by_user.out
    assert "Unsaved data may be lost" in refused_by_user.out
    assert target.writes() == []
    assert target.state_of("100") == "running"


def test_an_unmanaged_server_needs_the_extra_confirmation_first(
    target: Target,
) -> None:
    """US-001.07: a server not managed by this client needs the typed identifier."""
    session = target.session
    without_flag = target.power("start", "101", "--yes")
    wrong_flag = target.power("start", "101", "--yes", "--confirm-unmanaged", "100")
    session.interactive = True
    session.answers[:] = ["yes", "100"]
    wrong_typed = target.power("start", "101", *FAST)
    writes_before = target.writes()
    session.answers[:] = ["yes", "101"]
    typed = target.power("start", "101", *FAST)

    assert without_flag.code == 1
    assert "--confirm-unmanaged 101" in without_flag.err
    assert wrong_flag.code == 1
    assert wrong_typed.code == 1
    assert "not managed by this client" in wrong_typed.out
    assert writes_before == []
    assert typed.code == 0, typed.err
    assert "Retype the server identifier '101'" in session.questions[-1]
    assert target.writes() == [target.expected_write("101", PowerAction.START)]


def test_no_write_request_reaches_the_provider_without_the_confirmation(
    target: Target,
) -> None:
    """MIL-004 Go criterion 4: nothing is sent before the confirmation is given."""
    session = target.session
    results = [
        target.power("stop", "100"),
        target.power("restart", "101"),
        target.power("start", "101", "--yes"),
    ]
    session.interactive = True
    session.answers[:] = ["n", "y", "x"]
    results.append(target.power("stop", "100"))
    results.append(target.power("restart", "101", "--yes"))

    assert all(result.code == 1 for result in results)
    assert target.writes() == []


def test_a_successful_run_sends_exactly_the_one_allowed_request(
    target: Target,
) -> None:
    """MIL-004 Go criterion 5: one request, from the list of allowed writes."""
    stop = target.confirmed("stop", "100", *FAST)
    writes_after_stop = target.writes()
    restart = target.confirmed("restart", "100", *FAST)

    assert stop.code == 0, stop.err
    assert writes_after_stop == [target.expected_write("100", PowerAction.STOP)]
    assert restart.code == 0, restart.err
    assert target.writes() == [
        target.expected_write("100", PowerAction.STOP),
        target.expected_write("100", PowerAction.RESTART),
    ]
    assert set(target.writes()) <= target.allowed_writes


def test_a_server_already_in_the_state_sends_and_records_nothing(
    target: Target,
) -> None:
    """US-001.07: already in the requested state is reported, nothing is sent."""
    result = target.confirmed("start", "100", *FAST)

    assert result.code == 1
    assert "already running" in result.err
    assert target.writes() == []
    assert "operation" not in target.session.configuration_file.read_text("utf-8")


def test_a_missing_server_is_not_found_and_nothing_is_sent(target: Target) -> None:
    """US-001.07: a server that does not exist is reported, nothing is sent."""
    result = target.power("stop", "999", "--yes", *FAST)

    assert result.code == 1
    assert "not found" in result.err
    assert "Traceback" not in result.err
    assert target.writes() == []


def test_a_rejected_request_is_recorded_as_failed_and_not_repeated(
    target: Target,
) -> None:
    """US-001.05: a provider that rejects the request gives a failed operation."""
    target.reject_requests()

    result = target.confirmed("stop", "100", *FAST)
    shown = target.session.run("operation", target.operation_id(result))

    assert result.code == 1
    assert "rejected" in result.err
    assert output_state(shown.out) == "failed"
    assert len(target.writes()) == 1
    assert target.state_of("100") == "running"


def test_a_time_limit_ends_unknown_and_the_request_is_not_repeated(
    target: Target,
) -> None:
    """US-001.05: an operation that does not end in time is unknown, not success."""
    target.never_finish()

    result = target.confirmed(
        "stop", "100", "--timeout", "0.3", "--poll-interval", "0.05"
    )
    shown = target.session.run("operation", target.operation_id(result))

    assert result.code == 1
    assert output_state(result.out) == "unknown"
    assert "time limit passed" in result.out
    assert output_state(shown.out) == "unknown"
    assert target.writes() == [target.expected_write("100", PowerAction.STOP)]


def test_a_running_operation_can_be_followed_later(target: Target) -> None:
    """US-001.05: operation --follow ends a running operation and records it."""
    started = target.confirmed("stop", "100", "--no-wait")
    operation_id = target.operation_id(started)

    followed = target.session.run("operation", operation_id, "--follow", *FAST)
    shown = target.session.run("operation", operation_id)

    assert followed.code == 0, followed.err
    assert output_state(followed.out) == "succeeded"
    assert output_state(shown.out) == "succeeded"
    assert len(target.writes()) == 1


def test_an_operation_record_survives_a_new_process(
    target: Target, tmp_path: Path
) -> None:
    """MIL-004 task 7: the record is read back by another process."""
    done = target.confirmed("stop", "100", *FAST)
    operation_id = target.operation_id(done)
    environment = {
        **os.environ,
        "IAC_NODE_FORGE_CONFIG": str(target.session.configuration_file),
    }

    process = subprocess.run(
        [sys.executable, "-m", "iac_node_forge", "operation", operation_id],
        capture_output=True,
        text=True,
        env=environment,
        cwd=tmp_path,
        timeout=60,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    assert operation_id in process.stdout
    assert output_state(process.stdout) == "succeeded"


def test_no_credential_value_appears_in_a_record_a_file_or_an_error(
    target: Target,
) -> None:
    """MIL-004 Go criterion 6: no secret in the operation record, file or text."""
    session = target.session
    results = [
        target.confirmed("stop", "100", *FAST),
        target.confirmed("start", "100", *FAST),
        target.power("stop", "999", "--yes"),
        target.power("stop", "100"),
        session.run("operation", "no-such-operation"),
    ]
    target.reject_requests()
    results.append(target.confirmed("restart", "100", *FAST))
    target.fail_operations()
    target.use_wrong_credentials()
    results.append(target.power("start", "101", "--yes", "--confirm-unmanaged", "101"))
    results.append(session.run("servers"))

    file_text = session.configuration_file.read_text("utf-8")

    assert "operation" in file_text
    target.assert_no_secret(file_text)
    for result in results:
        target.assert_no_secret(result.out, result.err)
        assert "Traceback" not in result.err
