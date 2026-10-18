"""UC-004: preview, request and complete a provisioning with fakes."""

from dataclasses import dataclass, replace

import pytest

from iac_node_forge.application.dto import (
    Confirmation,
    ProvisionOutcome,
    ProvisionResult,
)
from iac_node_forge.application.errors import (
    ConfigurationNotFoundError,
    ProviderAuthenticationError,
)
from iac_node_forge.application.use_cases import (
    DesiredConfigurationValidator,
    ProvisionServer,
    TrackOperation,
)
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.operation import (
    Operation,
    OperationId,
    OperationState,
)
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.provider_value import ProviderValue
from iac_node_forge.domain.provision_intent import IntentState, ProvisionIntent
from tests.unit.application.fakes import (
    FakeClock,
    FakeConfigurationStore,
    FakeDesiredConfigurationStore,
    FakeOperationStore,
    FakeProviderResolver,
    FakeProvisionIntentStore,
    FakeProvisionProvider,
)
from tests.unit.application.samples import LAB, LAB_ID
from tests.unit.application.use_cases.provisioning_samples import (
    GOOD_VALUES,
    WEB_SERVER,
    desired,
    provider,
)

YES = Confirmation(given=True)
NO = Confirmation(given=False)


@dataclass(slots=True)
class Setup:
    fake: FakeProvisionProvider
    configurations: FakeConfigurationStore
    descriptions: FakeDesiredConfigurationStore
    intents: FakeProvisionIntentStore
    operations: FakeOperationStore
    clock: FakeClock
    use_case: ProvisionServer

    def request(self, name: str = "small", server: str = "") -> ProvisionResult:
        return self.use_case.request_provisioning("Lab", name, server, YES)

    def end(self, operation_id: str, state: OperationState) -> Operation:
        """Record the operation as ended in ``state``, as a follow would."""
        operation = self.operations.load(OperationId(operation_id))
        ended = operation.ended(state, operation.started_at, "ended by the test")
        self.operations.save(ended)
        return ended


def build(
    fake: FakeProvisionProvider | None = None,
    description: DesiredConfiguration | None = None,
    configuration: ProviderConfiguration = LAB,
) -> Setup:
    fake = fake or provider()
    configurations = FakeConfigurationStore()
    configurations.save(configuration)
    descriptions = FakeDesiredConfigurationStore()
    descriptions.save(description or desired())
    intents = FakeProvisionIntentStore()
    operations = FakeOperationStore()
    clock = FakeClock()
    resolver = FakeProviderResolver(fake)
    use_case = ProvisionServer(
        resolver,
        configurations,
        descriptions,
        intents,
        operations,
        DesiredConfigurationValidator(resolver),
        clock,
    )
    return Setup(
        fake, configurations, descriptions, intents, operations, clock, use_case
    )


def test_the_preview_states_what_will_be_created_and_the_cost() -> None:
    setup = build(provider(cost="committed for 12 months"))

    preview = setup.use_case.preview_provisioning("Lab", "small", "")

    assert preview.outcome is ProvisionOutcome.READY
    assert preview.provider_name == "Lab"
    assert preview.desired_name == "small"
    assert preview.server_name == "small"
    assert preview.target == "node pve1"
    assert preview.size == "kind lxc, cores 2"
    assert preview.image == desired().image
    assert preview.values == GOOD_VALUES
    assert preview.cost_consequence == "committed for 12 months"


def test_the_preview_uses_the_given_server_name_and_empty_cost_when_none() -> None:
    setup = build()

    preview = setup.use_case.preview_provisioning("Lab", "small", " web-2 ")

    assert preview.server_name == "web-2"
    assert preview.cost_consequence == ""


def test_the_preview_sends_and_records_nothing() -> None:
    setup = build()

    setup.use_case.preview_provisioning("Lab", "small", "")

    assert setup.fake.requests == []
    assert setup.intents.saved == []
    assert setup.operations.saved == []
    assert "reserve_identifier" not in setup.fake.reads


def test_an_unknown_description_is_invalid() -> None:
    setup = build()

    preview = setup.use_case.preview_provisioning("Lab", "missing", "")
    result = setup.use_case.request_provisioning("Lab", "missing", "", YES)

    assert preview.outcome is ProvisionOutcome.INVALID
    assert result.outcome is ProvisionOutcome.INVALID
    assert "missing" in result.reason
    assert setup.fake.requests == []


def test_a_description_that_is_no_longer_valid_is_checked_again() -> None:
    stale = desired(values=(*GOOD_VALUES[:2], ProviderValue("cores", "99")))
    setup = build(description=stale)

    result = setup.request()

    assert result.outcome is ProvisionOutcome.INVALID
    assert "'cores'" in result.reason
    assert setup.fake.requests == []
    assert setup.intents.saved == []


def test_a_provider_without_the_capability_is_unsupported() -> None:
    setup = build(provider(supported=False))

    result = setup.request()

    assert result.outcome is ProvisionOutcome.UNSUPPORTED
    assert setup.fake.requests == []
    assert setup.intents.saved == []


def test_a_server_with_the_name_at_the_provider_is_refused() -> None:
    setup = build()

    preview = setup.use_case.preview_provisioning("Lab", "small", WEB_SERVER.name)
    result = setup.request(server=WEB_SERVER.name)

    assert preview.outcome is ProvisionOutcome.NAME_EXISTS
    assert result.outcome is ProvisionOutcome.NAME_EXISTS
    assert "'web'" in result.reason
    assert setup.fake.requests == []
    assert setup.intents.saved == []


def test_without_the_confirmation_no_intent_and_no_request_is_made() -> None:
    setup = build()

    result = setup.use_case.request_provisioning("Lab", "small", "", NO)

    assert result.outcome is ProvisionOutcome.NOT_CONFIRMED
    assert result.operation is None
    assert setup.fake.requests == []
    assert setup.intents.saved == []
    assert setup.operations.saved == []
    assert "reserve_identifier" not in setup.fake.reads


def test_a_confirmed_request_saves_the_intent_before_it_sends_the_request() -> None:
    setup = build()
    seen: list[list[ProvisionIntent]] = []
    setup.fake.on_request = lambda: seen.append(list(setup.intents.saved))

    result = setup.request()

    assert result.outcome is ProvisionOutcome.REQUESTED
    [before_request] = seen
    assert [i.state for i in before_request] == [IntentState.PENDING]
    assert before_request[0].operation_id is None


def test_a_confirmed_request_records_the_operation_and_links_the_intent() -> None:
    setup = build()

    result = setup.request(server="web-2")

    assert result.outcome is ProvisionOutcome.REQUESTED
    assert setup.fake.requests == [("web-2", "200")]
    assert result.operation is not None
    assert result.operation.action is PowerAction.PROVISION
    assert result.operation.state is OperationState.RUNNING
    assert result.server_identifier == "200"
    recorded = setup.operations.load(OperationId(result.operation.operation_id))
    assert recorded.state is OperationState.RUNNING
    intent = setup.intents.saved[-1]
    assert intent.operation_id == recorded.operation_id
    assert intent.state is IntentState.PENDING
    assert intent.server_name == "web-2"
    assert intent.desired_name == "small"
    assert intent.reserved_identifier == "200"
    assert intent.image == desired().image
    assert intent.values == GOOD_VALUES


def test_a_provider_that_names_the_server_itself_has_no_reserved_identifier() -> None:
    setup = build(provider(reserves=False))

    result = setup.request()

    assert result.outcome is ProvisionOutcome.REQUESTED
    assert setup.intents.saved[0].reserved_identifier is None


def test_a_rejected_request_ends_the_intent_and_reports_the_state_left() -> None:
    setup = build()
    setup.fake.reject_with(ProviderAuthenticationError("the provider answered 403"))

    result = setup.request()

    assert result.outcome is ProvisionOutcome.REJECTED
    assert "403" in result.reason
    assert result.state_left == "no server named 'small' was found at the provider"
    assert result.operation is not None
    assert result.operation.state is OperationState.FAILED
    intent = setup.intents.saved[-1]
    assert intent.state is IntentState.ENDED
    assert intent.operation_id == result.operation.operation_id
    assert setup.operations.load(OperationId(result.operation.operation_id)).has_ended
    assert setup.fake.requests == []


def test_a_name_that_was_rejected_may_be_requested_again() -> None:
    setup = build()
    setup.fake.reject_with(ProviderAuthenticationError("403"))
    setup.request()
    setup.fake.reject_with(None)

    again = setup.request()

    assert again.outcome is ProvisionOutcome.REQUESTED


def test_a_retry_after_an_unknown_outcome_meets_the_pending_intent() -> None:
    setup = build()
    first = setup.request()
    assert first.operation is not None
    setup.end(first.operation.operation_id, OperationState.UNKNOWN)

    retry = setup.request()

    assert retry.outcome is ProvisionOutcome.INTENT_PENDING
    assert first.operation.operation_id in retry.reason
    assert len(setup.fake.requests) == 1
    assert len(setup.operations.saved) == 2


def test_a_retry_while_the_first_request_is_running_is_refused() -> None:
    setup = build()
    setup.request()

    preview = setup.use_case.preview_provisioning("Lab", "small", "")
    retry = setup.request()

    assert preview.outcome is ProvisionOutcome.INTENT_PENDING
    assert retry.outcome is ProvisionOutcome.INTENT_PENDING
    assert len(setup.fake.requests) == 1


def test_a_pending_intent_of_another_name_or_configuration_does_not_block() -> None:
    setup = build()
    other = ProvisionIntent(
        intent_id="i-other",  # type: ignore[arg-type]
        configuration_id=LAB_ID,
        desired_name="small",
        server_name="elsewhere",
        image="x",
        values=(),
        state=IntentState.PENDING,
        recorded_at=setup.clock(),
    )
    setup.intents.save(other)

    assert setup.request().outcome is ProvisionOutcome.REQUESTED


def test_an_unknown_configuration_is_an_error() -> None:
    setup = build()

    with pytest.raises(ConfigurationNotFoundError):
        setup.use_case.request_provisioning("Nope", "small", "", YES)


def requested(setup: Setup) -> str:
    result = setup.request()
    assert result.operation is not None
    return result.operation.operation_id


def test_a_succeeded_operation_records_the_server_as_managed_and_ends_the_intent() -> (
    None
):
    setup = build()
    operation_id = requested(setup)
    setup.end(operation_id, OperationState.SUCCEEDED)

    result = setup.use_case.complete_provisioning(operation_id)

    assert result.outcome is ProvisionOutcome.COMPLETED
    assert result.server_identifier == "200"
    [record] = setup.configurations.managed
    assert (record.configuration_id, record.server_identifier) == (LAB_ID, "200")
    assert record.recorded_at == setup.clock()
    assert setup.intents.saved[-1].state is IntentState.ENDED


def test_completing_twice_records_the_server_once() -> None:
    setup = build()
    operation_id = requested(setup)
    setup.end(operation_id, OperationState.SUCCEEDED)

    setup.use_case.complete_provisioning(operation_id)
    again = setup.use_case.complete_provisioning(operation_id)

    assert again.outcome is ProvisionOutcome.COMPLETED
    assert len(setup.configurations.managed) == 1


def test_a_failed_operation_ends_the_intent_and_reports_the_state_left() -> None:
    setup = build()
    operation_id = requested(setup)
    setup.end(operation_id, OperationState.FAILED)

    result = setup.use_case.complete_provisioning(operation_id)

    assert result.outcome is ProvisionOutcome.REJECTED
    assert result.reason == "ended by the test"
    assert result.state_left == "no server named 'small' was found at the provider"
    assert setup.configurations.managed == ()
    assert setup.intents.saved[-1].state is IntentState.ENDED


def test_a_failed_operation_names_a_server_the_provider_still_holds() -> None:
    setup = build()
    operation_id = requested(setup)
    setup.fake.add_server(
        replace(WEB_SERVER, server_identifier="200", name="half-made", state="stopped")
    )
    setup.end(operation_id, OperationState.FAILED)

    result = setup.use_case.complete_provisioning(operation_id)

    assert result.state_left == (
        "server '200' named 'half-made' exists in state stopped"
    )
    assert setup.configurations.managed == ()


def test_an_unknown_operation_changes_nothing_and_the_intent_stays_pending() -> None:
    setup = build()
    operation_id = requested(setup)
    setup.end(operation_id, OperationState.UNKNOWN)
    saves = len(setup.intents.saved)

    result = setup.use_case.complete_provisioning(operation_id)

    assert result.outcome is ProvisionOutcome.INCOMPLETE
    assert "unknown" in result.reason
    assert setup.configurations.managed == ()
    assert len(setup.intents.saved) == saves
    assert setup.intents.intents[setup.intents.saved[-1].intent_id].state is (
        IntentState.PENDING
    )


def test_a_running_operation_is_reported_as_not_finished() -> None:
    setup = build()
    operation_id = requested(setup)

    result = setup.use_case.complete_provisioning(operation_id)

    assert result.outcome is ProvisionOutcome.INCOMPLETE
    assert "running" in result.reason
    assert setup.configurations.managed == ()


def test_a_succeeded_operation_without_a_server_is_incomplete() -> None:
    setup = build(provider(reserves=False))
    operation_id = requested(setup)
    operation = setup.operations.load(OperationId(operation_id))
    setup.operations.save(
        replace(operation, server_identifier=None).ended(
            OperationState.SUCCEEDED, operation.started_at, "no instance named"
        )
    )

    result = setup.use_case.complete_provisioning(operation_id)

    assert result.outcome is ProvisionOutcome.INCOMPLETE
    assert "no instance named" in result.reason
    assert setup.configurations.managed == ()
    assert setup.intents.saved[-1].state is IntentState.PENDING


def test_an_operation_that_is_not_a_provision_is_invalid() -> None:
    setup = build()
    setup.operations.save(
        Operation(
            OperationId("op-power"),
            LAB_ID,
            "100",
            PowerAction.STOP,
            OperationState.RUNNING,
            setup.clock(),
        )
    )

    for operation_id in ("op-power", "op-missing"):
        result = setup.use_case.complete_provisioning(operation_id)
        assert result.outcome is ProvisionOutcome.INVALID


def test_a_provision_operation_without_an_intent_is_invalid() -> None:
    setup = build()
    setup.operations.save(
        Operation(
            OperationId("op-1"),
            LAB_ID,
            None,
            PowerAction.PROVISION,
            OperationState.RUNNING,
            setup.clock(),
        )
    )

    result = setup.use_case.complete_provisioning("op-1")

    assert result.outcome is ProvisionOutcome.INVALID


def test_the_operation_is_followed_through_the_provision_port() -> None:
    setup = build(provider(steps_to_finish=2))
    operation_id = requested(setup)
    resolver = FakeProviderResolver(setup.fake)
    tracker = TrackOperation(
        resolver, setup.configurations, setup.operations, setup.clock, setup.clock.sleep
    )

    followed = tracker.follow_operation(operation_id, 30, 1)
    result = setup.use_case.complete_provisioning(operation_id)

    assert followed.state is OperationState.SUCCEEDED
    assert resolver.provision_resolved
    assert resolver.power_resolved == []
    assert result.outcome is ProvisionOutcome.COMPLETED


def test_an_operation_that_never_ends_is_unknown_and_the_intent_stays_pending() -> None:
    setup = build(provider(steps_to_finish=10**6))
    operation_id = requested(setup)
    tracker = TrackOperation(
        FakeProviderResolver(setup.fake),
        setup.configurations,
        setup.operations,
        setup.clock,
        setup.clock.sleep,
    )

    followed = tracker.follow_operation(operation_id, 5, 1)
    result = setup.use_case.complete_provisioning(operation_id)
    retry = setup.request()

    assert followed.state is OperationState.UNKNOWN
    assert result.outcome is ProvisionOutcome.INCOMPLETE
    assert setup.configurations.managed == ()
    assert retry.outcome is ProvisionOutcome.INTENT_PENDING
    assert len(setup.fake.requests) == 1
