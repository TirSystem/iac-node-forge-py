"""``server define|provision|definitions`` and ``operation`` with fakes."""

from iac_node_forge.application.errors import ProviderAuthenticationError
from iac_node_forge.domain.operation import Operation, OperationId, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.provision_intent import (
    IntentId,
    IntentState,
    ProvisionIntent,
)
from iac_node_forge.presentation.cli.commands.server_power import is_power_request
from iac_node_forge.presentation.cli.commands.server_provision import (
    VERBS,
    is_provision_request,
)
from tests.unit.application.fakes import FakeProvisionProvider
from tests.unit.application.samples import LAB, LAB_ID
from tests.unit.application.use_cases.provisioning_samples import (
    GOOD_VALUES,
    IMAGE,
    desired,
    provider,
)
from tests.unit.presentation.cli.harness import CliRun, Harness

SET = ("--set", "node=pve1", "--set", "kind=lxc", "--set", "cores=2")
FAST = ("--poll-interval", "1", "--timeout", "30")


def provisioning(
    *,
    fake: FakeProvisionProvider | None = None,
    interactive: bool = False,
    answers: tuple[str, ...] = (),
    saved: bool = True,
) -> tuple[Harness, FakeProvisionProvider]:
    """Lab with the sample description ``small`` saved and WEB (100) existing."""
    fake = fake or provider()
    harness = Harness(provider=fake, interactive=interactive, answers=answers)
    harness.configurations.save(LAB)
    if saved:
        harness.desired_configurations.save(desired())
    return harness, fake


def operation_id(result: CliRun) -> str:
    for line in result.out.splitlines():
        if line.startswith("Operation:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no operation in: {result.out!r}")


def test_is_provision_request_tells_the_commands_from_the_read_command() -> None:
    assert VERBS == ("define", "provision", "definitions")
    assert is_provision_request(["server", "define", "Lab", "small", "--image", "x"])
    assert is_provision_request(["server", "provision", "Lab", "small"])
    assert is_provision_request(["server", "provision", "--help"])
    assert is_provision_request(["server", "definitions", "Lab"])
    assert not is_provision_request(["server", "provision", "100"])
    assert not is_provision_request(["server", "Lab", "100"])
    assert not is_provision_request(["server", "stop", "Lab", "100"])
    assert not is_provision_request(["servers"])
    assert not is_power_request(["server", "provision", "Lab", "small"])


def test_define_saves_a_valid_description_and_says_so() -> None:
    harness, _ = provisioning(saved=False)

    result = harness.run(
        "server", "define", "Lab", "small", "--image", IMAGE, "--key", "deploy", *SET
    )

    assert result.code == 0, result.err
    assert "'small' is valid and saved" in result.out
    assert "deploy" in result.out
    assert harness.desired_configurations.saved[LAB_ID, "small"].image == IMAGE


def test_define_with_an_unoffered_choice_reports_it_and_saves_nothing() -> None:
    harness, _ = provisioning(saved=False)

    result = harness.run(
        "server", "define", "Lab", "small", "--image", IMAGE, "--set", "node=pve9"
    )

    assert result.code == 1
    assert "not valid and was not saved" in result.err
    assert "'pve9'" in result.err
    assert "'kind' is required" in result.err
    assert harness.desired_configurations.saves == 0


def test_define_reports_every_problem_on_standard_error_only() -> None:
    harness, _ = provisioning(saved=False)

    result = harness.run("server", "define", "Lab", "small", "--image", "nope", *SET)

    assert result.code == 1
    assert "nope" in result.err
    assert "saved" not in result.out


def test_define_refuses_a_value_given_twice_and_a_malformed_value() -> None:
    harness, _ = provisioning(saved=False)

    twice = harness.run(
        "server",
        "define",
        "Lab",
        "s",
        "--image",
        IMAGE,
        "--set",
        "node=a",
        "--set",
        "node=b",
    )
    malformed = harness.run(
        "server", "define", "Lab", "s", "--image", IMAGE, "--set", "node"
    )

    assert twice.code == 2
    assert "more than once" in twice.err
    assert malformed.code == 2
    assert harness.desired_configurations.saves == 0


def test_define_needs_an_image() -> None:
    harness, _ = provisioning(saved=False)

    result = harness.run("server", "define", "Lab", "small")

    assert result.code == 2


def test_definitions_lists_the_saved_descriptions() -> None:
    harness, _ = provisioning()

    result = harness.run("server", "definitions", "Lab")

    assert result.code == 0
    assert "small" in result.out
    assert "deploy" in result.out
    assert "node=pve1" in result.out


def test_definitions_with_nothing_saved_says_so() -> None:
    harness, _ = provisioning(saved=False)

    result = harness.run("server", "definitions", "Lab")

    assert result.code == 0
    assert "No desired configurations" in result.out


def test_definitions_check_reports_a_description_that_is_no_longer_valid() -> None:
    harness, _ = provisioning()
    harness.desired_configurations.save(desired("stale", image="gone"))

    result = harness.run("server", "definitions", "Lab", "--check")

    assert result.code == 1
    assert "stale" in result.err
    assert "gone" in result.err


def test_provision_with_yes_shows_the_preview_and_creates_the_server() -> None:
    fake = provider(cost="the account is committed for 12 months")
    harness, _ = provisioning(fake=fake)

    result = harness.run("server", "provision", "Lab", "small", "--yes", *FAST)

    assert result.code == 0, result.err
    for text in (
        "About to create server 'small'",
        "Lab (Proxmox VE)",
        "node pve1",
        IMAGE,
        "kind lxc, cores 2",
        "node=pve1, kind=lxc, cores=2",
        "committed for 12 months",
        "State:",
        "succeeded",
        "Managed: server '200'",
    ):
        assert text in result.out
    assert fake.requests == [("small", "200")]
    assert [r.server_identifier for r in harness.configurations.managed] == ["200"]
    [intent] = {i.intent_id: i for i in harness.intents.saved}.values()
    assert intent.state is IntentState.ENDED


def test_provision_names_the_server_with_name() -> None:
    harness, fake = provisioning()

    result = harness.run(
        "server", "provision", "Lab", "small", "--name", "web-2", "--yes", *FAST
    )

    assert result.code == 0, result.err
    assert fake.requests == [("web-2", "200")]
    assert "Server name:" in result.out


def test_provision_states_that_the_provider_imposes_no_cost() -> None:
    harness, _ = provisioning()

    result = harness.run("server", "provision", "Lab", "small", "--yes", *FAST)

    assert "Cost:" in result.out
    assert "no commitment" in result.out


def test_provision_without_yes_and_without_a_terminal_sends_nothing() -> None:
    harness, fake = provisioning()

    result = harness.run("server", "provision", "Lab", "small")

    assert result.code == 1
    assert "About to create" in result.out
    assert "--yes" in result.err
    assert "No request was sent" in result.err
    assert fake.requests == []
    assert harness.intents.saved == []
    assert harness.operations.saved == []


def test_provision_asks_at_a_terminal_and_stops_on_no() -> None:
    harness, fake = provisioning(interactive=True, answers=("no",))

    result = harness.run("server", "provision", "Lab", "small", *FAST)

    assert result.code == 1
    assert "not confirmed" in result.err
    assert "Do you want to create server 'small'" in harness.asked[0]
    assert fake.requests == []
    assert harness.intents.saved == []


def test_provision_asks_at_a_terminal_and_goes_on_with_yes() -> None:
    harness, fake = provisioning(interactive=True, answers=("yes",))

    result = harness.run("server", "provision", "Lab", "small", *FAST)

    assert result.code == 0, result.err
    assert len(fake.requests) == 1


def test_provision_with_no_wait_prints_the_operation_and_how_to_follow_it() -> None:
    harness, fake = provisioning()

    result = harness.run("server", "provision", "Lab", "small", "--yes", "--no-wait")

    assert result.code == 0, result.err
    assert "running" in result.out
    assert f"operation {operation_id(result)} --follow" in result.out
    assert len(fake.requests) == 1
    assert harness.configurations.managed == ()


def test_operation_follow_completes_the_provision_and_records_the_server() -> None:
    harness, _ = provisioning()
    started = harness.run("server", "provision", "Lab", "small", "--yes", "--no-wait")

    followed = harness.run("operation", operation_id(started), "--follow", *FAST)

    assert followed.code == 0, followed.err
    assert "Managed: server '200'" in followed.out
    assert [r.server_identifier for r in harness.configurations.managed] == ["200"]


def test_a_description_that_is_not_saved_is_invalid() -> None:
    harness, fake = provisioning(saved=False)

    result = harness.run("server", "provision", "Lab", "small", "--yes")

    assert result.code == 1
    assert "no desired configuration 'small'" in result.err
    assert fake.requests == []


def test_a_provider_without_the_capability_is_unsupported() -> None:
    harness, fake = provisioning(fake=provider(supported=False))

    result = harness.run("server", "provision", "Lab", "small", "--yes")

    assert result.code == 1
    assert "provision_server" in result.err
    assert "capabilities" in result.err
    assert fake.requests == []


def test_an_existing_server_name_is_refused() -> None:
    harness, fake = provisioning()

    result = harness.run(
        "server", "provision", "Lab", "small", "--name", "web", "--yes"
    )

    assert result.code == 1
    assert "'web' already exists" in result.err
    assert "--name" in result.err
    assert fake.requests == []


def test_a_rejected_request_reports_the_state_left_and_ends_the_intent() -> None:
    harness, fake = provisioning()
    fake.reject_with(ProviderAuthenticationError("the provider answered 403"))

    result = harness.run("server", "provision", "Lab", "small", "--yes", *FAST)

    assert result.code == 1
    assert "rejected the creation" in result.err
    assert "403" in result.err
    assert "no server named 'small' was found" in result.err
    assert "failed" in result.out
    assert fake.requests == []
    assert harness.intents.saved[-1].state is IntentState.ENDED
    assert harness.configurations.managed == ()


def test_a_failed_operation_reports_the_state_left_and_records_no_server() -> None:
    harness, fake = provisioning()
    fake.make_operations_fail()

    result = harness.run("server", "provision", "Lab", "small", "--yes", *FAST)

    assert result.code == 1
    assert "the provision of server '200' failed" in result.err
    assert "State left at the provider" in result.err
    assert harness.configurations.managed == ()
    assert harness.intents.saved[-1].state is IntentState.ENDED


def test_a_time_limit_ends_unknown_and_a_second_provision_sends_nothing() -> None:
    harness, fake = provisioning(fake=provider(steps_to_finish=10**6))

    first = harness.run(
        "server",
        "provision",
        "Lab",
        "small",
        "--yes",
        "--timeout",
        "3",
        "--poll-interval",
        "1",
    )
    second = harness.run("server", "provision", "Lab", "small", "--yes", *FAST)

    assert first.code == 1
    assert "unknown" in first.out
    assert "time limit passed" in first.out
    assert second.code == 1
    assert "not finished" in second.err
    assert operation_id(first) in second.err
    assert len(fake.requests) == 1
    assert harness.configurations.managed == ()
    assert harness.intents.saved[-1].state is IntentState.PENDING


def _provision_without_server(harness: Harness, state: OperationState) -> str:
    """A recorded provision operation that names no server, with its intent."""
    harness.operations.save(
        Operation(
            OperationId("op-1"),
            LAB_ID,
            None,
            PowerAction.PROVISION,
            state,
            harness.clock(),
        )
    )
    harness.intents.save(
        ProvisionIntent(
            IntentId("i-1"),
            LAB_ID,
            "small",
            "web-9",
            IMAGE,
            GOOD_VALUES,
            IntentState.PENDING,
            harness.clock(),
            operation_id=OperationId("op-1"),
        )
    )
    return "op-1"


def test_an_unknown_provision_without_a_server_never_prints_none() -> None:
    harness, _ = provisioning(fake=provider(steps_to_finish=10**6))
    _provision_without_server(harness, OperationState.RUNNING)

    result = harness.run(
        "operation", "op-1", "--follow", "--timeout", "2", "--poll-interval", "1"
    )

    assert result.code == 1
    assert "unknown" in result.out
    assert "None" not in result.out + result.err
    assert "the outcome of the provision is unknown" in result.err
    assert "servers --configuration" in result.err
    assert harness.configurations.managed == ()


def test_a_succeeded_provision_without_a_server_is_incomplete() -> None:
    harness, _ = provisioning(fake=provider(steps_to_finish=1))
    _provision_without_server(harness, OperationState.RUNNING)

    result = harness.run("operation", "op-1", "--follow", *FAST)

    assert result.code == 1
    assert "incomplete" in result.err
    assert "provider named no server" in result.err
    assert "None" not in result.out + result.err
    assert harness.configurations.managed == ()


def test_operation_shows_the_completion_of_an_ended_provision() -> None:
    harness, _ = provisioning()
    started = harness.run("server", "provision", "Lab", "small", "--yes", *FAST)
    assert started.code == 0

    shown = harness.run("operation", operation_id(started))

    assert shown.code == 0
    assert "Managed: server '200'" in shown.out
    assert len(harness.configurations.managed) == 1


def test_operation_of_a_failed_provision_shows_the_state_left() -> None:
    harness, fake = provisioning()
    fake.make_operations_fail()
    failed = harness.run("server", "provision", "Lab", "small", "--yes", *FAST)

    shown = harness.run("operation", operation_id(failed))

    assert shown.code == 0
    assert "State left at the provider" in shown.out


def test_a_configuration_named_define_still_has_its_read_command() -> None:
    harness, _ = provisioning()
    harness.configurations.save(
        ProviderConfiguration(
            ConfigurationId("cfg-define"),
            ProviderKind.PROXMOX_VE,
            "define",
            "https://define.example:8006",
        )
    )

    result = harness.run("server", "define", "100")

    assert result.code == 0
    assert "Identifier:" in result.out
