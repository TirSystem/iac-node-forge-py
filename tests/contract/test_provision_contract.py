"""Provisioning behaviours every ServerProvisionPort implementation shares.

Each test runs against FakeProvisionProvider, ProxmoxProvider plus its mock and
ContaboProvider plus its mock, which shows that the fake and both adapters
agree (ADR-0007, MIL-005 Go criteria). No variant is skipped.
"""

import logging

import pytest

from iac_node_forge.application.capabilities import PROVISION_SERVER
from iac_node_forge.application.ports import ServerProvisionPort
from iac_node_forge.domain.operation import (
    MAX_DETAIL_LENGTH,
    Operation,
    OperationState,
)
from iac_node_forge.domain.power_action import PowerAction
from tests.contract.provider_under_test import ProvisionCase

MAX_POLLS = 20


def start(case: ProvisionCase) -> Operation:
    """Create one server the way the use case will: read first, then one write."""
    reserved = case.provision.reserve_identifier(case.configuration)
    return case.provision.request_provision(
        case.configuration, case.desired, case.server_name, reserved
    )


def follow(case: ProvisionCase, operation: Operation) -> list[Operation]:
    """Ask for the operation until it ends; the poll loop belongs to the use case."""
    seen: list[Operation] = []
    for _ in range(MAX_POLLS):
        operation = case.provision.operation_state(case.configuration, operation)
        seen.append(operation)
        if operation.has_ended:
            break
    return seen


def test_provider_satisfies_server_provision_port(
    provision_case: ProvisionCase,
) -> None:
    assert isinstance(provision_case.provision, ServerProvisionPort)


def test_provision_capability_is_declared_by_the_provider(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case

    names = {c.name for c in case.provider.discover_capabilities(case.configuration)}

    assert PROVISION_SERVER in names


def test_value_rules_are_not_empty_and_declare_the_required_names(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case

    rules = case.provision.value_rules(case.configuration)

    assert rules
    assert {rule.name for rule in rules if rule.required} >= case.required_value_names
    assert len({rule.name for rule in rules}) == len(rules)
    for rule in rules:
        checks = [rule.option_kind, rule.allowed or None, rule.minimum, rule.maximum]
        assert any(check is not None for check in checks)


def test_the_value_rules_are_satisfied_by_the_description_of_the_case(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case
    rules = {r.name: r for r in case.provision.value_rules(case.configuration)}

    for value in case.desired.values:
        rule = rules.get(value.name)
        if rule is None or rule.option_kind is None:
            continue
        offered = {
            option.value
            for option in case.provider.list_server_options(
                case.configuration, rule.option_kind
            )
        }
        assert value.value in offered


def test_the_reads_before_the_request_send_no_write(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case

    case.provision.value_rules(case.configuration)
    case.provision.reserve_identifier(case.configuration)
    case.provision.cost_consequence(case.configuration, case.desired)

    assert case.write_requests() == []


def test_the_identifier_is_reserved_only_by_a_provider_that_lets_the_caller_choose(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case

    reserved = case.provision.reserve_identifier(case.configuration)

    if case.reserves_identifier:
        assert reserved is not None
        assert reserved.isdecimal()
    else:
        assert reserved is None


def test_the_cost_is_stated_only_by_a_provider_that_makes_a_commitment(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case

    cost = case.provision.cost_consequence(case.configuration, case.desired)

    if case.states_cost:
        assert cost is not None
        assert "12 months" in cost
        assert "commit" in cost
    else:
        assert cost is None


def test_creating_returns_a_running_provision_operation(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case

    operation = start(case)

    assert operation.state is OperationState.RUNNING
    assert operation.action is PowerAction.PROVISION
    assert operation.server_identifier
    assert operation.configuration_id == case.configuration.configuration_id
    assert operation.started_at.tzinfo is not None
    assert operation.finished_at is None


def test_following_the_operation_reaches_succeeded_with_a_server_identifier(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case
    operation = start(case)

    seen = follow(case, operation)

    final = seen[-1]
    assert final.state is OperationState.SUCCEEDED
    assert final.server_identifier
    assert final.finished_at is not None
    assert final.finished_at >= final.started_at
    assert final.operation_id == operation.operation_id
    assert final.action is PowerAction.PROVISION
    assert len(seen) < MAX_POLLS


def test_the_operation_is_reported_running_before_it_ends(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case

    seen = follow(case, start(case))

    assert seen[0].state is OperationState.RUNNING
    assert seen[0].finished_at is None


def test_the_created_server_appears_in_list_servers_under_its_name(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case
    final = follow(case, start(case))[-1]

    servers = case.provider.list_servers(case.configuration)

    created = [s for s in servers if s.server_identifier == final.server_identifier]
    assert [s.name for s in created] == [case.server_name]


def test_an_ended_operation_is_returned_unchanged(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case
    final = follow(case, start(case))[-1]

    again = case.provision.operation_state(case.configuration, final)

    assert again == final


def test_provider_rejection_gives_failed_operation_with_a_reason_and_no_secret(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case
    case.reject_creation()

    operation = start(case)

    assert operation.state is OperationState.FAILED
    assert operation.action is PowerAction.PROVISION
    assert operation.detail
    assert len(operation.detail) <= MAX_DETAIL_LENGTH
    assert operation.finished_at is not None
    assert not [v for v in case.secret_values if v in operation.detail]
    if case.rejection_hint is not None:
        assert case.rejection_hint in operation.detail


def test_a_rejected_creation_leaves_no_server_behind(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case
    before = case.provider.list_servers(case.configuration)
    case.reject_creation()

    start(case)

    assert case.provider.list_servers(case.configuration) == before


def test_operation_that_the_provider_ends_in_failure_is_failed_with_a_reason(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case
    case.fail_operations()

    final = follow(case, start(case))[-1]

    assert final.state is OperationState.FAILED
    assert final.detail
    assert final.finished_at is not None
    running = [
        s
        for s in case.provider.list_servers(case.configuration)
        if s.name == case.server_name and s.state == "running"
    ]
    assert running == []


def test_the_only_write_received_is_the_one_creation(
    provision_case: ProvisionCase,
) -> None:
    case = provision_case

    follow(case, start(case))
    written = case.write_requests()

    assert len(written) == 1
    assert set(written) <= case.allowed_write_requests


def test_secret_values_never_appear_in_operations_errors_or_logs(
    provision_case: ProvisionCase, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    case = provision_case
    texts: list[str] = [repr(case.provision)]
    operation = start(case)
    for step in [operation, *follow(case, operation)]:
        texts.extend(_texts_of(step))
    case.reject_creation()
    texts.extend(_texts_of(start(case)))
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
