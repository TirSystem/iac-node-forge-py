"""``server remove`` and ``operation`` for a removal with fakes."""

from iac_node_forge.application.errors import ProviderAuthenticationError
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.operation import OperationId, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.removal_kind import RemovalKind
from iac_node_forge.domain.server import Server
from iac_node_forge.presentation.cli.commands.server_power import is_power_request
from iac_node_forge.presentation.cli.commands.server_provision import (
    is_provision_request,
)
from iac_node_forge.presentation.cli.commands.server_remove import is_remove_request
from tests.unit.application.fakes import FakeRemovalProvider
from tests.unit.application.samples import DB, LAB, LAB_ID, WEB
from tests.unit.presentation.cli.harness import CliRun, Harness

FAST = ("--poll-interval", "1", "--timeout", "30")
CANCELLED = Server("300", "old", "cancelled", "1 vCPU", (), False)
MANAGED_DB = ManagedServerRecord(LAB_ID, "101", Harness().clock.now)


def removing(
    *,
    kind: RemovalKind = RemovalKind.DESTROY,
    managed: bool = False,
    interactive: bool = False,
    answers: tuple[str, ...] = (),
) -> tuple[Harness, FakeRemovalProvider]:
    """Lab with WEB (running), DB (stopped) and a cancelled server."""
    harness = Harness(interactive=interactive, answers=answers)
    fake = FakeRemovalProvider(
        servers=(WEB, DB, CANCELLED), kind=kind, clock=harness.clock
    )
    harness.provider = fake
    harness.configurations.save(LAB)
    if managed:
        harness.configurations.managed = (MANAGED_DB,)
    return harness, fake


def operation_id(result: CliRun) -> str:
    for line in result.out.splitlines():
        if line.startswith("Operation:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no operation in: {result.out!r}")


def test_is_remove_request_tells_the_command_from_the_other_server_commands() -> None:
    assert is_remove_request(["server", "remove", "Lab", "101"])
    assert is_remove_request(["server", "remove", "Lab", "101", "--yes"])
    assert is_remove_request(["server", "remove", "--help"])
    assert is_remove_request(["server", "remove"])
    assert not is_remove_request(["server", "remove", "101"])
    assert not is_remove_request(["server", "Lab", "101"])
    assert not is_remove_request(["server", "stop", "Lab", "101"])
    assert not is_remove_request(["servers", "remove", "Lab", "101"])
    assert not is_power_request(["server", "remove", "Lab", "101"])
    assert not is_provision_request(["server", "remove", "Lab", "101"])


def test_a_configuration_named_remove_still_shows_a_server() -> None:
    harness, _ = removing()
    harness.configurations.save(
        ProviderConfiguration(
            ConfigurationId("cfg-r"), LAB.provider_kind, "remove", LAB.address
        )
    )

    result = harness.run("server", "remove", "100")

    assert result.code == 0, result.err
    assert "web" in result.out
    assert harness.operations.saved == []


def test_remove_prints_the_preview_with_the_consequence_before_asking() -> None:
    harness, fake = removing(managed=True)

    result = harness.run("server", "remove", "Lab", "101")

    assert result.code == 1
    for text in (
        "About to remove server '101' (db) at provider 'Lab'",
        "Lab (Proxmox VE)",
        "stopped",
        "Removal:",
        "destroy",
        "This destroys server 101 now.",
    ):
        assert text in result.out
    assert "Managed:" in result.out
    assert "--yes" in result.err
    assert "No request was sent" in result.err
    assert fake.requests == []
    assert harness.operations.saved == []


def test_remove_with_yes_destroys_a_managed_server_and_ends_its_record() -> None:
    harness, fake = removing(managed=True)

    result = harness.run("server", "remove", "Lab", "101", "--yes", *FAST)

    assert result.code == 0, result.err
    assert fake.requests == ["101"]
    assert "Requested: removal of server '101'" in result.out
    assert "Consequence:" in result.out
    assert "succeeded" in result.out
    assert "State left at the provider: server '101' no longer exists" in result.out
    assert "the record of server '101' is ended" in result.out
    assert harness.configurations.managed_servers(LAB_ID) == ()
    [saved] = {o.operation_id: o for o in harness.operations.saved}.values()
    assert saved.action is PowerAction.REMOVE
    assert saved.consequence == "This destroys server 101 now."


def test_remove_of_an_unmanaged_server_needs_the_second_confirmation() -> None:
    harness, fake = removing()

    plain = harness.run("server", "remove", "Lab", "101", "--yes")
    wrong = harness.run(
        "server", "remove", "Lab", "101", "--yes", "--confirm-unmanaged", "102"
    )
    right = harness.run(
        "server", "remove", "Lab", "101", "--yes", "--confirm-unmanaged", "101", *FAST
    )

    assert plain.code == 1
    assert "not managed by this client" in plain.out
    assert "--confirm-unmanaged 101" in plain.err
    assert wrong.code == 1
    assert "does not match" in wrong.err
    assert right.code == 0, right.err
    assert "was not managed by this client; no record was changed" in right.out
    assert fake.requests == ["101"]


def test_remove_asks_for_the_identifier_at_a_terminal() -> None:
    harness, fake = removing(managed=True, interactive=True, answers=("101",))

    result = harness.run("server", "remove", "Lab", "101", *FAST)

    assert result.code == 0, result.err
    assert "Type the server identifier '101'" in harness.asked[0]
    assert len(harness.asked) == 1
    assert fake.requests == ["101"]


def test_remove_asks_twice_for_an_unmanaged_server_at_a_terminal() -> None:
    harness, fake = removing(interactive=True, answers=("101", "101"))

    result = harness.run("server", "remove", "Lab", "101", *FAST)

    assert result.code == 0, result.err
    assert len(harness.asked) == 2
    assert "again" in harness.asked[1]
    assert fake.requests == ["101"]


def test_remove_does_not_ask_twice_when_the_first_typing_is_wrong() -> None:
    harness, fake = removing(interactive=True, answers=("db",))

    result = harness.run("server", "remove", "Lab", "101", *FAST)

    assert result.code == 1
    assert len(harness.asked) == 1
    assert "does not match" in result.err
    assert "type '101' exactly" in result.err
    assert fake.requests == []


def test_remove_stops_on_an_empty_answer() -> None:
    harness, fake = removing(interactive=True, answers=("",))

    result = harness.run("server", "remove", "Lab", "101", "--yes", *FAST)

    assert result.code == 1
    assert "not confirmed" in result.err
    assert fake.requests == []


def test_remove_takes_the_second_typing_from_the_option_at_a_terminal() -> None:
    harness, fake = removing(interactive=True, answers=("101",))

    result = harness.run(
        "server", "remove", "Lab", "101", "--confirm-unmanaged", "101", *FAST
    )

    assert result.code == 0, result.err
    assert len(harness.asked) == 1
    assert fake.requests == ["101"]


def test_remove_refuses_a_running_server_and_says_how_to_stop_it() -> None:
    harness, fake = removing(managed=True)

    result = harness.run("server", "remove", "Lab", "100", "--yes")

    assert result.code == 1
    assert "must be shut down first" in result.err
    assert "iac-node-forge server shutdown cfg-lab 100" in result.err
    assert "Consequence:" not in result.out
    assert fake.requests == []
    assert harness.operations.saved == []


def test_remove_reports_an_already_cancelled_instance() -> None:
    harness, fake = removing(kind=RemovalKind.CANCEL)

    result = harness.run("server", "remove", "Lab", "300", "--yes")

    assert result.code == 1
    assert "already removed" in result.err
    assert fake.requests == []
    assert harness.operations.saved == []


def test_remove_reports_an_unknown_server_and_an_unsupported_provider() -> None:
    harness, fake = removing()
    unsupported = Harness()
    unsupported.provider = FakeRemovalProvider(servers=(DB,), supported=False)
    unsupported.configurations.save(LAB)

    missing = harness.run("server", "remove", "Lab", "999", "--yes")
    refused = unsupported.run("server", "remove", "Lab", "101", "--yes")

    assert missing.code == 1
    assert "'999'" in missing.err
    assert "servers --configuration" in missing.err
    assert refused.code == 1
    assert "capability remove_server" in refused.err
    assert "capabilities" in refused.err
    assert fake.requests == []


def test_a_rejected_removal_prints_the_operation_and_the_state_left() -> None:
    harness, fake = removing(managed=True)
    fake.reject_with(ProviderAuthenticationError("answered 403"))

    result = harness.run("server", "remove", "Lab", "101", "--yes", *FAST)

    assert result.code == 1
    assert "rejected the removal of server '101': answered 403" in result.err
    assert "State left at the provider: server '101' exists in state stopped" in (
        result.err
    )
    assert "failed" in result.out
    assert "Consequence:" in result.out
    assert fake.requests == []
    assert harness.configurations.managed_servers(LAB_ID) == (MANAGED_DB,)


def test_a_failed_operation_keeps_the_record_and_reports_the_state_left() -> None:
    harness, fake = removing(managed=True)
    fake.make_operations_fail()

    result = harness.run("server", "remove", "Lab", "101", "--yes", *FAST)

    assert result.code == 1
    assert "the removal of server '101' failed" in result.err
    assert "State left at the provider: server '101' exists" in result.err
    assert harness.configurations.managed_servers(LAB_ID) == (MANAGED_DB,)


def test_a_lost_answer_is_reported_unknown_and_not_repeated() -> None:
    harness, fake = removing(managed=True)
    fake.answer_unknown()

    result = harness.run("server", "remove", "Lab", "101", "--yes", *FAST)

    assert result.code == 1
    assert "the outcome of the removal of server '101' is unknown" in result.err
    assert "State left at the provider" in result.err
    assert fake.requests == ["101"]
    assert harness.configurations.managed_servers(LAB_ID) == (MANAGED_DB,)


def test_remove_with_no_wait_then_operation_follow_completes_it() -> None:
    harness, fake = removing(managed=True)

    started = harness.run("server", "remove", "Lab", "101", "--yes", "--no-wait")
    followed = harness.run("operation", operation_id(started), "--follow", *FAST)
    shown = harness.run("operation", operation_id(started))

    assert started.code == 0, started.err
    assert "running" in started.out
    assert "operation" in started.out and "--follow" in started.out
    assert harness.configurations.managed_servers(LAB_ID) == ()
    assert followed.code == 0, followed.err
    assert "the record of server '101' is ended" in followed.out
    assert shown.code == 0
    assert "Consequence:" in shown.out
    assert fake.requests == ["101"]
    ended = harness.operations.load(OperationId(operation_id(started)))
    assert ended.state is OperationState.SUCCEEDED


def test_a_cancellation_prints_the_effective_date_in_the_detail() -> None:
    harness, _ = removing(kind=RemovalKind.CANCEL, managed=True)

    result = harness.run("server", "remove", "Lab", "101", "--yes", *FAST)

    assert result.code == 0, result.err
    assert "cancellation of server 101" in result.out
    assert "cancelled, effective 2026-11-18" in result.out
    assert "Removal:       cancel" in result.out


def test_remove_help_is_usage_not_a_failure() -> None:
    harness, _ = removing()

    result = harness.run("server", "remove", "--help")
    missing = harness.run("server", "remove")

    assert result.code == 0
    assert "--confirm-unmanaged" in result.out
    assert missing.code == 2
