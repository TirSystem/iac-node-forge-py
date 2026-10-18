"""UC-012: preview, request and complete a removal with fakes."""

from dataclasses import dataclass

import pytest

from iac_node_forge.application.dto import (
    Confirmation,
    RemovalOutcome,
    RemovalResult,
)
from iac_node_forge.application.errors import (
    ConfigurationNotFoundError,
    ProviderAuthenticationError,
    ProviderUnreachableError,
)
from iac_node_forge.application.use_cases import RemoveServer, TrackOperation
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.operation import (
    Operation,
    OperationId,
    OperationState,
)
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.removal_kind import RemovalKind
from iac_node_forge.domain.server import Server
from tests.unit.application.fakes import (
    FakeClock,
    FakeConfigurationStore,
    FakeOperationStore,
    FakeProviderResolver,
    FakeRemovalProvider,
)
from tests.unit.application.samples import DB, LAB, LAB_ID, WEB

CANCELLED = Server("300", "old", "cancelled", "1 vCPU", (), False)
SERVERS = (WEB, DB, CANCELLED)
MANAGED_DB = ManagedServerRecord(LAB_ID, "101", FakeClock().now)


@dataclass(slots=True)
class Setup:
    fake: FakeRemovalProvider
    configurations: FakeConfigurationStore
    operations: FakeOperationStore
    clock: FakeClock
    use_case: RemoveServer
    tracker: TrackOperation

    def typed(self, identifier: str, again: str | None = None) -> Confirmation:
        return Confirmation(True, identifier, again)

    def request_db(self, again: str | None = None) -> RemovalResult:
        return self.use_case.request_removal("Lab", "101", self.typed("101", again))

    def finish(self, operation_id: str) -> Operation:
        """Follow the operation to its end, as the command line does."""
        self.tracker.follow_operation(operation_id, 30, 1)
        return self.operations.load(OperationId(operation_id))


def build(
    kind: RemovalKind = RemovalKind.DESTROY,
    managed: tuple[ManagedServerRecord, ...] = (),
    supported: bool = True,
    effective_date: str | None = "2026-11-18",
    servers: tuple[Server, ...] = SERVERS,
) -> Setup:
    clock = FakeClock()
    fake = FakeRemovalProvider(
        servers=servers,
        kind=kind,
        supported=supported,
        effective_date=effective_date,
        clock=clock,
    )
    configurations = FakeConfigurationStore(managed)
    configurations.save(LAB)
    operations = FakeOperationStore()
    resolver = FakeProviderResolver(fake)
    return Setup(
        fake,
        configurations,
        operations,
        clock,
        RemoveServer(resolver, configurations, operations, clock),
        TrackOperation(resolver, configurations, operations, clock, clock.sleep),
    )


def test_the_preview_states_the_kind_and_the_consequence_and_sends_nothing() -> None:
    setup = build(managed=(MANAGED_DB,))

    preview = setup.use_case.preview_removal("Lab", "101")

    assert preview.outcome is RemovalOutcome.READY
    assert preview.provider_name == "Lab"
    assert (preview.server_identifier, preview.server_name) == ("101", "db")
    assert preview.server_state == "stopped"
    assert preview.managed
    assert preview.kind is RemovalKind.DESTROY
    assert preview.consequence == "This destroys server 101 now."
    assert setup.fake.requests == []
    assert setup.operations.operations == {}


def test_the_preview_of_a_cancellation_names_the_effective_date_rule() -> None:
    setup = build(RemovalKind.CANCEL)

    preview = setup.use_case.preview_removal("Lab", "101")

    assert preview.kind is RemovalKind.CANCEL
    assert not preview.managed
    assert "effective date the provider reports" in preview.consequence


def test_a_server_the_provider_marks_managed_counts_as_managed() -> None:
    setup = build(servers=(Server("101", "db", "stopped", "1 vCPU", (), True),))

    assert setup.use_case.preview_removal("Lab", "101").managed


def test_an_unsupported_provider_is_reported_before_the_server_is_read() -> None:
    setup = build(supported=False)

    preview = setup.use_case.preview_removal("Lab", "101")
    result = setup.request_db("101")

    assert preview.outcome is RemovalOutcome.UNSUPPORTED
    assert "remove_server" in preview.reason
    assert result.outcome is RemovalOutcome.UNSUPPORTED
    assert setup.fake.requests == []
    assert "removal_block" not in setup.fake.reads


def test_a_server_the_provider_does_not_have_is_not_found() -> None:
    setup = build()

    result = setup.use_case.request_removal("Lab", "999", setup.typed("999", "999"))

    assert result.outcome is RemovalOutcome.NOT_FOUND
    assert result.operation is None
    assert setup.fake.requests == []
    assert setup.operations.operations == {}


def test_a_running_server_is_refused_and_not_stopped() -> None:
    setup = build()

    preview = setup.use_case.preview_removal("Lab", "100")
    result = setup.use_case.request_removal("Lab", "100", setup.typed("100", "100"))

    assert preview.outcome is RemovalOutcome.RUNNING_REFUSED
    assert "shut down" in preview.reason
    assert preview.consequence == ""
    assert result.outcome is RemovalOutcome.RUNNING_REFUSED
    assert setup.fake.requests == []
    assert setup.operations.operations == {}


def test_a_cancelled_server_is_reported_as_already_removed() -> None:
    setup = build(RemovalKind.CANCEL)

    result = setup.use_case.request_removal("Lab", "300", setup.typed("300", "300"))

    assert result.outcome is RemovalOutcome.ALREADY_REMOVED
    assert "cancelled" in result.reason
    assert setup.fake.requests == []
    assert setup.operations.operations == {}


@pytest.mark.parametrize(
    ("confirmation", "outcome"),
    [
        (Confirmation(False, "101", "101"), RemovalOutcome.NOT_CONFIRMED),
        (Confirmation(True), RemovalOutcome.NOT_CONFIRMED),
        (Confirmation(True, "db", "db"), RemovalOutcome.IDENTIFIER_MISMATCH),
        (Confirmation(True, "10", "10"), RemovalOutcome.IDENTIFIER_MISMATCH),
        (Confirmation(True, "101 ", "101"), RemovalOutcome.IDENTIFIER_MISMATCH),
        (Confirmation(True, "101"), RemovalOutcome.NOT_CONFIRMED),
        (Confirmation(True, "101", "102"), RemovalOutcome.IDENTIFIER_MISMATCH),
    ],
)
def test_an_incomplete_or_wrong_confirmation_sends_and_records_nothing(
    confirmation: Confirmation, outcome: RemovalOutcome
) -> None:
    setup = build()

    result = setup.use_case.request_removal("Lab", "101", confirmation)

    assert result.outcome is outcome
    assert result.operation is None
    assert setup.fake.requests == []
    assert setup.operations.operations == {}


def test_a_managed_server_needs_the_identifier_typed_once() -> None:
    setup = build(managed=(MANAGED_DB,))

    wrong = setup.use_case.request_removal("Lab", "101", Confirmation(True, "x"))
    right = setup.use_case.request_removal("Lab", "101", Confirmation(True, "101"))

    assert wrong.outcome is RemovalOutcome.IDENTIFIER_MISMATCH
    assert right.outcome is RemovalOutcome.REQUESTED
    assert setup.fake.requests == ["101"]


def test_a_request_sends_one_request_and_records_the_consequence() -> None:
    setup = build(managed=(MANAGED_DB,))

    result = setup.use_case.request_removal("Lab", "101", Confirmation(True, "101"))

    assert result.outcome is RemovalOutcome.REQUESTED
    assert result.consequence == "This destroys server 101 now."
    assert result.operation is not None
    assert result.operation.action is PowerAction.REMOVE
    assert result.operation.state is OperationState.RUNNING
    assert result.operation.consequence == result.consequence
    saved = setup.operations.load(OperationId(result.operation.operation_id))
    assert saved.consequence == "This destroys server 101 now."
    assert setup.fake.requests == ["101"]
    assert setup.configurations.managed_servers(LAB_ID) == (MANAGED_DB,)


def test_a_rejected_request_is_a_failed_operation_and_the_record_stays() -> None:
    setup = build(managed=(MANAGED_DB,))
    setup.fake.reject_with(ProviderAuthenticationError("answered 403"))

    result = setup.request_db()

    assert result.outcome is RemovalOutcome.REJECTED
    assert result.reason == "answered 403"
    assert result.operation is not None
    assert result.operation.state is OperationState.FAILED
    assert result.consequence == "This destroys server 101 now."
    assert result.state_left == "server '101' exists in state stopped"
    assert setup.fake.requests == []
    assert setup.configurations.managed_servers(LAB_ID) == (MANAGED_DB,)
    [saved] = setup.operations.operations.values()
    assert saved.consequence == "This destroys server 101 now."


def test_a_lost_answer_is_an_unknown_operation_and_is_not_repeated() -> None:
    setup = build(managed=(MANAGED_DB,))
    setup.fake.answer_unknown()

    result = setup.request_db()
    completed = setup.use_case.complete_removal(
        result.operation.operation_id if result.operation else ""
    )

    assert result.outcome is RemovalOutcome.REQUESTED
    assert result.operation is not None
    assert result.operation.state is OperationState.UNKNOWN
    assert setup.fake.requests == ["101"]
    assert completed.outcome is RemovalOutcome.INCOMPLETE
    assert "unknown" in completed.reason
    assert not completed.managed_ended
    assert setup.configurations.managed_servers(LAB_ID) == (MANAGED_DB,)
    assert setup.fake.requests == ["101"]


def test_a_succeeded_removal_ends_the_managed_record_and_keeps_it() -> None:
    setup = build(managed=(MANAGED_DB,))
    requested = setup.request_db()
    assert requested.operation is not None
    operation_id = requested.operation.operation_id

    setup.finish(operation_id)
    result = setup.use_case.complete_removal(operation_id)

    assert result.outcome is RemovalOutcome.COMPLETED
    assert result.managed_ended
    assert result.consequence == "This destroys server 101 now."
    assert result.state_left == "server '101' no longer exists at the provider"
    assert setup.configurations.managed_servers(LAB_ID) == ()
    [kept] = setup.configurations.managed
    assert kept.ended_by_operation_id == operation_id
    assert kept.ended_at == setup.clock.now


def test_completing_again_ends_nothing_more() -> None:
    setup = build(managed=(MANAGED_DB,))
    requested = setup.request_db()
    assert requested.operation is not None
    operation_id = requested.operation.operation_id
    setup.finish(operation_id)
    setup.use_case.complete_removal(operation_id)

    again = setup.use_case.complete_removal(operation_id)

    assert again.outcome is RemovalOutcome.COMPLETED
    assert not again.managed_ended
    assert len(setup.configurations.ended) == 1


def test_a_server_recorded_after_the_removal_started_is_not_ended() -> None:
    setup = build(managed=(MANAGED_DB,))
    requested = setup.request_db()
    assert requested.operation is not None
    operation_id = requested.operation.operation_id
    setup.finish(operation_id)
    setup.use_case.complete_removal(operation_id)
    newer = ManagedServerRecord(LAB_ID, "101", setup.clock.now)
    setup.configurations.save_managed_server(newer)

    again = setup.use_case.complete_removal(operation_id)

    assert not again.managed_ended
    assert setup.configurations.managed_servers(LAB_ID) == (newer,)


def test_a_server_that_was_not_managed_has_no_record_to_end() -> None:
    setup = build()
    requested = setup.request_db("101")
    assert requested.operation is not None
    operation_id = requested.operation.operation_id
    setup.finish(operation_id)

    result = setup.use_case.complete_removal(operation_id)

    assert result.outcome is RemovalOutcome.COMPLETED
    assert not result.managed_ended
    assert setup.configurations.ended == []


def test_a_cancellation_reports_the_effective_date_as_the_state_left() -> None:
    setup = build(RemovalKind.CANCEL, managed=(MANAGED_DB,))
    requested = setup.request_db()
    assert requested.operation is not None
    operation_id = requested.operation.operation_id
    setup.finish(operation_id)

    result = setup.use_case.complete_removal(operation_id)

    assert result.outcome is RemovalOutcome.COMPLETED
    assert result.state_left == (
        "server '101' exists in state cancelled; cancelled, effective 2026-11-18"
    )


def test_a_cancellation_without_a_reported_date_still_ends_the_record() -> None:
    setup = build(RemovalKind.CANCEL, managed=(MANAGED_DB,), effective_date=None)
    requested = setup.request_db()
    assert requested.operation is not None
    setup.finish(requested.operation.operation_id)

    result = setup.use_case.complete_removal(requested.operation.operation_id)

    assert result.managed_ended
    assert result.state_left is not None
    assert "no date was reported" in result.state_left


def test_a_failed_operation_reports_the_state_left_and_ends_nothing() -> None:
    setup = build(managed=(MANAGED_DB,))
    setup.fake.make_operations_fail()
    requested = setup.request_db()
    assert requested.operation is not None
    operation_id = requested.operation.operation_id
    setup.finish(operation_id)

    result = setup.use_case.complete_removal(operation_id)

    assert result.outcome is RemovalOutcome.REJECTED
    assert result.reason == "the fake removal failed"
    assert result.state_left == "server '101' exists in state stopped"
    assert not result.managed_ended
    assert setup.configurations.managed_servers(LAB_ID) == (MANAGED_DB,)


def test_a_running_operation_is_incomplete_and_changes_nothing() -> None:
    setup = build(managed=(MANAGED_DB,))
    requested = setup.request_db()
    assert requested.operation is not None

    result = setup.use_case.complete_removal(requested.operation.operation_id)

    assert result.outcome is RemovalOutcome.INCOMPLETE
    assert setup.configurations.managed_servers(LAB_ID) == (MANAGED_DB,)


def test_a_state_that_cannot_be_read_does_not_stop_the_recording() -> None:
    setup = build(managed=(MANAGED_DB,))
    requested = setup.request_db()
    assert requested.operation is not None
    operation_id = requested.operation.operation_id
    setup.finish(operation_id)
    setup.fake.list_servers = _unreachable  # type: ignore[method-assign]

    result = setup.use_case.complete_removal(operation_id)

    assert result.outcome is RemovalOutcome.COMPLETED
    assert result.managed_ended
    assert result.state_left == "the state left at the provider could not be read"


def test_an_unknown_operation_or_another_action_is_not_found() -> None:
    setup = build()
    setup.operations.save(
        Operation(
            OperationId("op-power"),
            LAB_ID,
            "100",
            PowerAction.STOP,
            OperationState.RUNNING,
            setup.clock.now,
        )
    )

    missing = setup.use_case.complete_removal("nope")
    other = setup.use_case.complete_removal("op-power")

    assert missing.outcome is RemovalOutcome.NOT_FOUND
    assert other.outcome is RemovalOutcome.NOT_FOUND
    assert "not a removal" in other.reason


def test_an_unknown_configuration_raises() -> None:
    setup = build()

    with pytest.raises(ConfigurationNotFoundError):
        setup.use_case.preview_removal("Nowhere", "101")


def _unreachable(*args: object, **kwargs: object) -> tuple[Server, ...]:
    raise ProviderUnreachableError("no route")
