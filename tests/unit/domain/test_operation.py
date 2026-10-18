"""Operation, PowerAction and OperationState keep their invariants."""

import dataclasses
from datetime import UTC, datetime, timedelta, timezone

import pytest

from iac_node_forge.domain.errors import (
    EmptyValueError,
    NaiveTimestampError,
    OperationStateError,
    OperationTextTooLongError,
)
from iac_node_forge.domain.operation import (
    MAX_CONSEQUENCE_LENGTH,
    MAX_DETAIL_LENGTH,
    MAX_PROVIDER_REFERENCE_LENGTH,
    Operation,
    OperationId,
    OperationState,
    bound_detail,
)
from iac_node_forge.domain.power_action import POWER_ACTIONS, PowerAction
from iac_node_forge.domain.provider_configuration import ConfigurationId

START = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
LATER = START + timedelta(seconds=5)


def make(**overrides: object) -> Operation:
    values: dict[str, object] = {
        "operation_id": OperationId("op-1"),
        "configuration_id": ConfigurationId("cfg-1"),
        "server_identifier": "100",
        "action": PowerAction.START,
        "state": OperationState.RUNNING,
        "started_at": START,
    }
    values.update(overrides)
    return Operation(**values)  # type: ignore[arg-type]


def test_power_actions_are_the_four_documented_actions() -> None:
    assert [a.value for a in POWER_ACTIONS] == [
        "start",
        "shutdown",
        "stop",
        "restart",
    ]


def test_provision_is_an_action_of_an_operation_but_not_a_power_action() -> None:
    assert PowerAction.PROVISION.value == "provision"
    assert PowerAction.PROVISION not in POWER_ACTIONS


def test_remove_is_an_action_of_an_operation_but_not_a_power_action() -> None:
    assert PowerAction.REMOVE.value == "remove"
    assert PowerAction.REMOVE not in POWER_ACTIONS


def test_operation_state_has_the_four_documented_states() -> None:
    assert [s.value for s in OperationState] == [
        "running",
        "succeeded",
        "failed",
        "unknown",
    ]


def test_running_operation_without_end_time_is_valid() -> None:
    operation = make()

    assert not operation.has_ended
    assert operation.finished_at is None


@pytest.mark.parametrize(
    "state",
    [OperationState.SUCCEEDED, OperationState.FAILED, OperationState.UNKNOWN],
)
def test_ended_state_needs_an_end_time(state: OperationState) -> None:
    with pytest.raises(OperationStateError):
        make(state=state)


@pytest.mark.parametrize(
    "state",
    [OperationState.SUCCEEDED, OperationState.FAILED, OperationState.UNKNOWN],
)
def test_ended_state_with_end_time_is_valid(state: OperationState) -> None:
    operation = make(state=state, finished_at=LATER)

    assert operation.has_ended


def test_running_operation_with_end_time_is_rejected() -> None:
    with pytest.raises(OperationStateError):
        make(finished_at=LATER)


def test_end_time_before_start_is_rejected() -> None:
    with pytest.raises(OperationStateError):
        make(state=OperationState.FAILED, finished_at=START - timedelta(seconds=1))


def test_naive_start_is_rejected() -> None:
    with pytest.raises(NaiveTimestampError):
        make(started_at=datetime(2026, 10, 15, 12, 0))


def test_naive_end_is_rejected() -> None:
    with pytest.raises(NaiveTimestampError):
        make(state=OperationState.FAILED, finished_at=datetime(2026, 10, 15, 13, 0))


def test_timezone_other_than_utc_is_accepted() -> None:
    zone = timezone(timedelta(hours=2))

    operation = make(started_at=datetime(2026, 10, 15, 14, 0, tzinfo=zone))

    assert operation.started_at.utcoffset() == timedelta(hours=2)


@pytest.mark.parametrize(
    "field_name", ["operation_id", "configuration_id", "server_identifier"]
)
def test_blank_identifier_is_rejected(field_name: str) -> None:
    with pytest.raises(EmptyValueError):
        make(**{field_name: "  "})


def test_detail_at_the_limit_is_accepted() -> None:
    operation = make(detail="x" * MAX_DETAIL_LENGTH)

    assert operation.detail is not None
    assert len(operation.detail) == MAX_DETAIL_LENGTH


def test_detail_over_the_limit_is_rejected_not_cut() -> None:
    with pytest.raises(OperationTextTooLongError):
        make(detail="x" * (MAX_DETAIL_LENGTH + 1))


def test_blank_detail_is_rejected() -> None:
    with pytest.raises(EmptyValueError):
        make(detail=" ")


def test_provider_reference_over_the_limit_is_rejected() -> None:
    with pytest.raises(OperationTextTooLongError):
        make(provider_reference="u" * (MAX_PROVIDER_REFERENCE_LENGTH + 1))


def test_bound_detail_collapses_whitespace_to_one_line() -> None:
    assert bound_detail("  task\nfailed \t badly ") == "task failed badly"


def test_bound_detail_cuts_a_long_text_to_the_limit_with_ellipsis() -> None:
    result = bound_detail("y" * (MAX_DETAIL_LENGTH * 3))

    assert result is not None
    assert len(result) == MAX_DETAIL_LENGTH
    assert result.endswith("...")


def test_bound_detail_gives_none_for_blank_text() -> None:
    assert bound_detail(" \n ") is None


def test_bounded_text_always_fits_an_operation() -> None:
    detail = bound_detail("z" * 10_000)

    assert make(detail=detail).detail == detail


def test_ended_returns_a_new_operation_in_the_final_state() -> None:
    running = make(provider_reference="UPID:pve1:1")

    ended = running.ended(OperationState.SUCCEEDED, LATER, None)

    assert running.state is OperationState.RUNNING
    assert ended.state is OperationState.SUCCEEDED
    assert ended.finished_at == LATER
    assert ended.provider_reference == "UPID:pve1:1"


def test_ended_to_running_is_rejected() -> None:
    with pytest.raises(OperationStateError):
        make().ended(OperationState.RUNNING, LATER, None)


def test_ended_operation_is_not_ended_again() -> None:
    done = make(state=OperationState.SUCCEEDED, finished_at=LATER)

    with pytest.raises(OperationStateError):
        done.ended(OperationState.FAILED, LATER, "late")


def test_operation_is_frozen() -> None:
    operation = make()

    with pytest.raises(dataclasses.FrozenInstanceError):
        operation.state = OperationState.FAILED  # type: ignore[misc]


def test_operation_fields_are_the_documented_attributes_only() -> None:
    names = [f.name for f in dataclasses.fields(Operation)]

    assert names == [
        "operation_id",
        "configuration_id",
        "server_identifier",
        "action",
        "state",
        "started_at",
        "finished_at",
        "provider_reference",
        "detail",
        "consequence",
    ]


def test_operation_fields_have_no_credential_type() -> None:
    hints = {f.name: str(f.type) for f in dataclasses.fields(Operation)}

    assert not [name for name, text in hints.items() if "Credentials" in text]


def test_provision_operation_may_have_no_server_identifier_yet() -> None:
    operation = make(action=PowerAction.PROVISION, server_identifier=None)

    assert operation.server_identifier is None
    assert not operation.has_ended


def test_blank_server_identifier_is_still_rejected() -> None:
    with pytest.raises(EmptyValueError):
        make(server_identifier=" ")


def test_consequence_is_kept_as_given_and_is_empty_by_default() -> None:
    text = "This destroys server 100 now."

    assert make().consequence is None
    assert make(action=PowerAction.REMOVE, consequence=text).consequence == text


def test_consequence_survives_the_end_of_the_operation() -> None:
    running = make(action=PowerAction.REMOVE, consequence="This destroys it.")

    ended = running.ended(OperationState.SUCCEEDED, LATER, "cancelled, effective soon")

    assert ended.consequence == "This destroys it."
    assert ended.detail == "cancelled, effective soon"


def test_consequence_at_the_bound_is_accepted_and_beyond_it_is_rejected() -> None:
    assert make(consequence="x" * MAX_CONSEQUENCE_LENGTH)

    with pytest.raises(OperationTextTooLongError):
        make(consequence="x" * (MAX_CONSEQUENCE_LENGTH + 1))


def test_blank_consequence_is_rejected() -> None:
    with pytest.raises(EmptyValueError):
        make(consequence="  ")
