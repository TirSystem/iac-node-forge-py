"""The power commands and ``operation`` with fakes: UC-008 and UC-006 at the CLI."""

import pytest

from iac_node_forge.application.errors import ProviderAuthenticationError
from iac_node_forge.domain.operation import OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.server import Server
from iac_node_forge.presentation.cli.commands.server_power import is_power_request
from tests.unit.application.fakes import FakePoweredProvider, FakeProvider
from tests.unit.application.samples import DB, LAB, SECRET, WEB, WEB_IS_MANAGED
from tests.unit.presentation.cli.harness import Harness


def powered(
    *,
    servers: tuple[Server, ...] = (WEB, DB),
    steps_to_finish: int = 2,
    interactive: bool = False,
    answers: tuple[str, ...] = (),
) -> tuple[Harness, FakePoweredProvider]:
    """Lab with WEB (100, running, managed) and DB (101, stopped, unmanaged)."""
    provider = FakePoweredProvider(
        servers=servers,
        steps_to_finish=steps_to_finish,
    )
    harness = Harness(provider=provider, interactive=interactive, answers=answers)
    harness.configurations.save(LAB)
    harness.configurations.managed = (WEB_IS_MANAGED,)
    return harness, provider


def test_is_power_request_tells_the_power_commands_from_the_read_command() -> None:
    assert is_power_request(["server", "stop", "Lab", "100"])
    assert is_power_request(["server", "start", "Lab", "100", "--yes"])
    assert not is_power_request(["server", "Lab", "100"])
    assert not is_power_request(["server", "start", "100"])
    assert is_power_request(["server", "start", "--help"])
    assert not is_power_request(["servers"])
    assert not is_power_request([])


def test_the_read_command_still_works_for_a_configuration_named_start() -> None:
    harness, _ = powered()
    harness.configurations.save(
        ProviderConfiguration(
            ConfigurationId("cfg-start"),
            ProviderKind.PROXMOX_VE,
            "start",
            "https://start.example:8006",
        )
    )

    result = harness.run("server", "start", "100")

    assert result.code == 0
    assert "Identifier:" in result.out


def test_stop_without_yes_and_without_a_terminal_sends_nothing() -> None:
    harness, provider = powered()

    result = harness.run("server", "stop", "Lab", "100")

    assert result.code == 1
    assert "--yes" in result.err
    assert "No request was sent" in result.err
    assert provider.power.requests == []
    assert harness.operations.saved == []
    assert harness.asked == []


def test_stop_with_yes_sends_one_request_and_reports_the_state() -> None:
    harness, provider = powered()

    result = harness.run("server", "stop", "Lab", "100", "--yes")

    assert result.code == 0, result.err
    assert provider.power.requests == [("100", PowerAction.STOP)]
    assert "succeeded" in result.out
    assert "Server state" in result.out
    assert "stopped" in result.out
    assert [o.state for o in harness.operations.saved] == [
        OperationState.RUNNING,
        OperationState.SUCCEEDED,
    ]


def test_an_unmanaged_server_needs_the_identifier_without_a_terminal() -> None:
    harness, provider = powered()

    result = harness.run("server", "start", "Lab", "101", "--yes")

    assert result.code == 1
    assert "not managed by this client" in result.err
    assert "--confirm-unmanaged 101" in result.err
    assert provider.power.requests == []


def test_an_unmanaged_server_is_started_with_the_identifier_given() -> None:
    harness, provider = powered()

    result = harness.run(
        "server", "start", "Lab", "101", "--yes", "--confirm-unmanaged", "101"
    )

    assert result.code == 0, result.err
    assert provider.power.requests == [("101", PowerAction.START)]


def test_a_wrong_identifier_for_an_unmanaged_server_sends_nothing() -> None:
    harness, provider = powered()

    result = harness.run(
        "server", "start", "Lab", "101", "--yes", "--confirm-unmanaged", "100"
    )

    assert result.code == 1
    assert "does not match" in result.err
    assert provider.power.requests == []


def test_no_wait_prints_the_operation_as_running_and_exits_zero() -> None:
    harness, provider = powered()

    result = harness.run("server", "restart", "Lab", "100", "--yes", "--no-wait")

    assert result.code == 0
    saved = harness.operations.saved
    assert len(saved) == 1
    assert saved[0].operation_id in result.out
    assert "running" in result.out
    assert provider.power.requests == [("100", PowerAction.RESTART)]
    assert harness.clock.sleeps == []


def test_a_time_limit_ends_as_unknown_with_the_reason_and_exit_one() -> None:
    harness, provider = powered(steps_to_finish=1000)

    result = harness.run(
        "server",
        "stop",
        "Lab",
        "100",
        "--yes",
        "--timeout",
        "5",
        "--poll-interval",
        "2",
    )

    assert result.code == 1
    assert "unknown" in result.out
    assert "time limit passed" in result.out
    assert "unknown" in result.err
    assert provider.power.requests == [("100", PowerAction.STOP)]
    assert harness.operations.saved[-1].state is OperationState.UNKNOWN


def test_a_failed_operation_exits_one_with_the_cause() -> None:
    harness, provider = powered()
    provider.power.make_operations_fail()

    result = harness.run("server", "stop", "Lab", "100", "--yes")

    assert result.code == 1
    assert "failed" in result.out
    assert "the fake task failed" in result.err
    assert "Hint:" in result.err


def test_a_provider_that_rejects_the_request_exits_one_and_records_it() -> None:
    harness, provider = powered()
    provider.power.reject_with(ProviderAuthenticationError("answered 403"))

    result = harness.run("server", "stop", "Lab", "100", "--yes")

    assert result.code == 1
    assert "rejected" in result.err
    assert "answered 403" in result.err
    assert "failed" in result.out
    assert len(harness.operations.saved) == 1
    assert provider.power.requests == []


def test_an_unsupported_action_exits_one_with_a_hint_and_sends_nothing() -> None:
    harness = Harness(provider=FakeProvider(servers=(WEB,)))
    harness.configurations.save(LAB)

    result = harness.run("server", "stop", "Lab", "100", "--yes")

    assert result.code == 1
    assert "does not support" in result.err
    assert "capabilities" in result.err
    assert harness.operations.saved == []


def test_a_server_already_in_the_state_exits_one_and_records_nothing() -> None:
    harness, provider = powered()

    result = harness.run("server", "start", "Lab", "100", "--yes")

    assert result.code == 1
    assert "already running" in result.err
    assert provider.power.requests == []
    assert harness.operations.saved == []


def test_a_missing_server_exits_one_and_sends_nothing() -> None:
    harness, provider = powered()

    result = harness.run("server", "stop", "Lab", "999", "--yes")

    assert result.code == 1
    assert "not found" in result.err
    assert provider.power.requests == []


def test_an_unknown_configuration_exits_one() -> None:
    harness, _ = powered()

    result = harness.run("server", "stop", "Nope", "100", "--yes")

    assert result.code == 1
    assert "Nope" in result.err


def test_interactive_run_states_the_action_and_asks_once_for_a_managed_server() -> None:
    harness, provider = powered(interactive=True, answers=("yes",))

    result = harness.run("server", "stop", "Lab", "100")

    assert result.code == 0, result.err
    assert len(harness.asked) == 1
    assert "About to stop server '100'" in result.out
    assert "Unsaved data may be lost" in result.out
    assert "not managed by this client" not in result.out
    assert provider.power.requests == [("100", PowerAction.STOP)]


def test_interactive_restart_warns_about_unsaved_data() -> None:
    harness, _ = powered(interactive=True, answers=("no",))

    result = harness.run("server", "restart", "Lab", "100")

    assert "Unsaved data may be lost" in result.out


def test_interactive_shutdown_does_not_claim_data_loss() -> None:
    harness, _ = powered(interactive=True, answers=("no",))

    result = harness.run("server", "shutdown", "Lab", "100")

    assert "Unsaved data may be lost" not in result.out


def test_interactive_run_for_an_unmanaged_server_asks_to_retype_the_identifier() -> (
    None
):
    harness, provider = powered(interactive=True, answers=("y", "101"))

    result = harness.run("server", "start", "Lab", "101")

    assert result.code == 0, result.err
    assert "This server is not managed by this client." in result.out
    assert len(harness.asked) == 2
    assert "Retype the server identifier '101'" in harness.asked[1]
    assert provider.power.requests == [("101", PowerAction.START)]


def test_interactive_run_with_a_wrong_retyped_identifier_sends_nothing() -> None:
    harness, provider = powered(interactive=True, answers=("yes", "100"))

    result = harness.run("server", "start", "Lab", "101")

    assert result.code == 1
    assert "does not match" in result.err
    assert provider.power.requests == []


@pytest.mark.parametrize("answer", ["no", "", "maybe", "n"])
def test_interactive_run_without_a_yes_sends_nothing(answer: str) -> None:
    harness, provider = powered(interactive=True, answers=(answer,))

    result = harness.run("server", "stop", "Lab", "100")

    assert result.code == 1
    assert "cancelled" in result.err
    assert provider.power.requests == []
    assert harness.operations.saved == []


def test_interactive_yes_option_skips_the_question_but_not_the_retyping() -> None:
    harness, provider = powered(interactive=True, answers=("101",))

    result = harness.run("server", "start", "Lab", "101", "--yes")

    assert result.code == 0, result.err
    assert len(harness.asked) == 1
    assert "Retype" in harness.asked[0]
    assert provider.power.requests == [("101", PowerAction.START)]


def test_interactive_options_given_up_front_ask_nothing() -> None:
    harness, provider = powered(interactive=True)

    result = harness.run(
        "server", "start", "Lab", "101", "--yes", "--confirm-unmanaged", "101"
    )

    assert result.code == 0, result.err
    assert harness.asked == []
    assert provider.power.requests == [("101", PowerAction.START)]


def test_interactive_run_reports_already_in_state_without_asking() -> None:
    harness, provider = powered(interactive=True)

    result = harness.run("server", "start", "Lab", "100")

    assert result.code == 1
    assert "already running" in result.err
    assert harness.asked == []
    assert provider.power.requests == []


def test_end_of_input_while_asking_exits_one_and_sends_nothing() -> None:
    harness, provider = powered(interactive=True, answers=())

    result = harness.run("server", "stop", "Lab", "100")

    assert result.code == 1
    assert provider.power.requests == []


def test_operation_shows_a_recorded_operation() -> None:
    harness, _ = powered()
    harness.run("server", "restart", "Lab", "100", "--yes", "--no-wait")
    operation_id = harness.operations.saved[0].operation_id

    result = harness.run("operation", operation_id)

    assert result.code == 0
    assert operation_id in result.out
    assert "restart" in result.out
    assert "running" in result.out


def test_operation_follow_ends_a_running_operation() -> None:
    harness, _ = powered()
    harness.run("server", "restart", "Lab", "100", "--yes", "--no-wait")
    operation_id = harness.operations.saved[0].operation_id

    result = harness.run("operation", operation_id, "--follow")

    assert result.code == 0, result.err
    assert "succeeded" in result.out
    assert harness.operations.saved[-1].state is OperationState.SUCCEEDED


def test_operation_exits_one_for_an_unknown_identifier() -> None:
    harness, _ = powered()

    result = harness.run("operation", "nope")

    assert result.code == 1
    assert "nope" in result.err
    assert "Traceback" not in result.err


@pytest.mark.parametrize(
    "option", [("--timeout", "-1"), ("--poll-interval", "0"), ("--timeout", "x")]
)
def test_a_wrong_time_option_is_a_usage_error(option: tuple[str, str]) -> None:
    harness, provider = powered()

    result = harness.run("server", "stop", "Lab", "100", "--yes", *option)

    assert result.code == 2
    assert provider.power.requests == []


def test_a_power_command_without_an_identifier_is_a_usage_error() -> None:
    harness, _ = powered()

    result = harness.run("server", "stop", "Lab", "--yes")

    assert result.code == 2


def test_help_of_a_power_command_lists_the_confirmation_options() -> None:
    harness, _ = powered()

    result = harness.run("server", "stop", "Lab", "100", "--help")

    assert result.code == 0
    for word in ("--yes", "--confirm-unmanaged", "--no-wait", "--timeout"):
        assert word in result.out


def test_no_secret_reaches_the_output_of_a_power_command() -> None:
    harness, provider = powered()
    provider.power.reject_with(ProviderAuthenticationError("answered 403"))

    result = harness.run("server", "stop", "Lab", "100", "--yes")

    assert SECRET not in result.out + result.err
