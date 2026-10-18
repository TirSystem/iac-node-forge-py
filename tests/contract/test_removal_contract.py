"""Removal behaviours every ServerRemovalPort implementation shares.

Each test runs against FakeRemovalProvider, ProxmoxProvider plus its mock and
ContaboProvider plus its mock, which shows that the fake and both adapters
agree (ADR-0008, MIL-006 Go criterion 2). No variant is skipped.
"""

import logging

import pytest

from iac_node_forge.application.capabilities import REMOVE_SERVER
from iac_node_forge.application.ports import ServerRemovalPort
from iac_node_forge.domain.operation import (
    MAX_CONSEQUENCE_LENGTH,
    MAX_DETAIL_LENGTH,
    Operation,
    OperationState,
)
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.removal_kind import RemovalKind
from iac_node_forge.domain.server import Server
from tests.contract.provider_under_test import RemovalCase

MAX_POLLS = 20
KIND_WORD = {RemovalKind.DESTROY: "destroy", RemovalKind.CANCEL: "cancel"}


def read(case: RemovalCase, identifier: str) -> Server:
    """Read a server the way the use case will, before any removal request."""
    return case.provider.get_server(case.configuration, identifier)


def start(case: RemovalCase, identifier: str | None = None) -> Operation:
    """Remove one server the way the use case will: read first, then one write."""
    server = read(case, identifier or case.removable_id)
    return case.removal.request_removal(case.configuration, server)


def follow(case: RemovalCase, operation: Operation) -> list[Operation]:
    """Ask for the operation until it ends; the poll loop belongs to the use case."""
    seen: list[Operation] = []
    for _ in range(MAX_POLLS):
        operation = case.removal.operation_state(case.configuration, operation)
        seen.append(operation)
        if operation.has_ended:
            break
    return seen


def listed(case: RemovalCase) -> dict[str, Server]:
    servers = case.provider.list_servers(case.configuration)
    return {server.server_identifier: server for server in servers}


def test_provider_satisfies_server_removal_port(removal_case: RemovalCase) -> None:
    assert isinstance(removal_case.removal, ServerRemovalPort)


def test_remove_capability_is_declared_by_the_provider(
    removal_case: RemovalCase,
) -> None:
    case = removal_case

    names = {c.name for c in case.provider.discover_capabilities(case.configuration)}

    assert REMOVE_SERVER in names


def test_the_removal_kind_is_the_one_the_provider_means_by_removal(
    removal_case: RemovalCase,
) -> None:
    case = removal_case

    assert case.removal.removal_kind(case.configuration) is case.kind


def test_the_consequence_is_a_bounded_text_that_names_the_kind_and_the_server(
    removal_case: RemovalCase,
) -> None:
    case = removal_case

    text = case.removal.consequence(case.configuration, read(case, case.removable_id))

    assert text.strip()
    assert len(text) <= MAX_CONSEQUENCE_LENGTH
    assert KIND_WORD[case.kind] in text
    assert case.removable_id in text
    assert not [v for v in case.secret_values if v in text]


def test_a_stopped_server_is_not_blocked(removal_case: RemovalCase) -> None:
    case = removal_case

    block = case.removal.removal_block(
        case.configuration, read(case, case.removable_id)
    )

    assert block is None


def test_a_server_the_provider_blocks_is_reported_with_its_block(
    removal_case: RemovalCase,
) -> None:
    case = removal_case
    case.prepare_blocked()

    block = case.removal.removal_block(case.configuration, read(case, case.blocked_id))

    assert block is case.expected_block


def test_the_reads_before_the_request_send_no_write(removal_case: RemovalCase) -> None:
    case = removal_case
    server = read(case, case.removable_id)

    case.removal.removal_kind(case.configuration)
    case.removal.removal_block(case.configuration, server)
    case.removal.consequence(case.configuration, server)

    assert case.write_requests() == []


def test_removing_returns_a_running_remove_operation_for_the_server(
    removal_case: RemovalCase,
) -> None:
    case = removal_case

    operation = start(case)

    assert operation.state is OperationState.RUNNING
    assert operation.action is PowerAction.REMOVE
    assert operation.server_identifier == case.removable_id
    assert operation.configuration_id == case.configuration.configuration_id
    assert operation.started_at.tzinfo is not None
    assert operation.finished_at is None


def test_following_the_operation_reaches_succeeded(removal_case: RemovalCase) -> None:
    case = removal_case
    operation = start(case)

    seen = follow(case, operation)

    final = seen[-1]
    assert final.state is OperationState.SUCCEEDED
    assert final.server_identifier == case.removable_id
    assert final.finished_at is not None
    assert final.finished_at >= final.started_at
    assert final.operation_id == operation.operation_id
    assert final.action is PowerAction.REMOVE
    assert len(seen) < MAX_POLLS


def test_the_operation_is_reported_running_before_it_ends(
    removal_case: RemovalCase,
) -> None:
    case = removal_case

    seen = follow(case, start(case))

    assert seen[0].state is OperationState.RUNNING
    assert seen[0].finished_at is None


def test_the_removed_server_is_gone_or_cancelled_as_the_kind_says(
    removal_case: RemovalCase,
) -> None:
    case = removal_case
    follow(case, start(case))

    servers = listed(case)

    if case.kind is RemovalKind.DESTROY:
        assert case.removable_id not in servers
    else:
        assert servers[case.removable_id].state == "cancelled"


def test_removing_one_server_leaves_the_other_untouched(
    removal_case: RemovalCase,
) -> None:
    case = removal_case
    before = listed(case)[case.blocked_id]

    follow(case, start(case))

    assert listed(case)[case.blocked_id] == before


def test_an_ended_operation_is_returned_unchanged(removal_case: RemovalCase) -> None:
    case = removal_case
    final = follow(case, start(case))[-1]

    again = case.removal.operation_state(case.configuration, final)

    assert again == final


def test_provider_rejection_gives_failed_operation_with_a_reason_and_no_secret(
    removal_case: RemovalCase,
) -> None:
    case = removal_case
    case.reject_removal()

    operation = start(case)

    assert operation.state is OperationState.FAILED
    assert operation.action is PowerAction.REMOVE
    assert operation.server_identifier == case.removable_id
    assert operation.detail
    assert len(operation.detail) <= MAX_DETAIL_LENGTH
    assert operation.finished_at is not None
    assert not [v for v in case.secret_values if v in operation.detail]
    if case.rejection_hint is not None:
        assert case.rejection_hint in operation.detail


def test_a_rejected_removal_leaves_the_server_as_it_was(
    removal_case: RemovalCase,
) -> None:
    case = removal_case
    before = case.provider.list_servers(case.configuration)
    case.reject_removal()

    start(case)

    assert case.provider.list_servers(case.configuration) == before


def test_operation_that_the_provider_ends_in_failure_is_failed_with_a_reason(
    removal_case: RemovalCase,
) -> None:
    case = removal_case
    case.fail_operations()

    final = follow(case, start(case))[-1]

    assert final.state is OperationState.FAILED
    assert final.detail
    assert final.finished_at is not None
    left = listed(case).get(case.removable_id)
    assert left is not None
    assert left.state != "cancelled"


def test_a_lost_answer_gives_unknown_and_the_request_is_not_repeated(
    removal_case: RemovalCase,
) -> None:
    case = removal_case
    case.lose_answers()

    operation = start(case)

    assert operation.state is OperationState.UNKNOWN
    assert operation.server_identifier == case.removable_id
    assert operation.detail
    assert "may have reached the provider" in operation.detail
    assert operation.finished_at is not None
    assert len(case.write_requests()) <= 1


def test_the_only_write_received_is_the_one_removal(removal_case: RemovalCase) -> None:
    case = removal_case

    follow(case, start(case))
    written = case.write_requests()

    assert len(written) == 1
    assert set(written) <= case.allowed_write_requests


def test_secret_values_never_appear_in_operations_errors_or_logs(
    removal_case: RemovalCase, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    case = removal_case
    texts: list[str] = [repr(case.removal)]
    server = read(case, case.removable_id)
    texts.append(case.removal.consequence(case.configuration, server))
    operation = start(case)
    for step in [operation, *follow(case, operation)]:
        texts.extend(_texts_of(step))
    case.reject_removal()
    texts.extend(_texts_of(start(case, case.blocked_id)))
    texts.extend(record.getMessage() for record in caplog.records)

    leaked = [v for v in case.secret_values if any(v in text for text in texts)]
    assert texts
    assert not leaked


def _texts_of(operation: Operation) -> list[str]:
    return [
        repr(operation),
        str(operation),
        operation.detail or "",
        operation.provider_reference or "",
    ]
