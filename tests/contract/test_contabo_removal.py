"""Removal behaviours specific to the Contabo adapter, against the mock API."""

import pytest

from iac_node_forge.application.errors import (
    ProviderError,
    ProviderRateLimitError,
    ProviderServiceError,
    ProviderTimeoutError,
    ServerNotFoundError,
)
from iac_node_forge.application.ports import RemovalBlock
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.operation import Operation, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.removal_kind import RemovalKind
from iac_node_forge.domain.server import Server
from iac_node_forge.infrastructure.contabo import ContaboProvider
from tests.contract.conftest import CONTABO_VALUES, contabo_configuration_for
from tests.contract.mock_contabo import CANCEL_DATE, MockContaboServer

MAX_POLLS = 20


@pytest.fixture
def configuration(mock_contabo: MockContaboServer) -> ProviderConfiguration:
    return contabo_configuration_for(mock_contabo.address)


@pytest.fixture
def provider(mock_contabo: MockContaboServer) -> ContaboProvider:
    return ContaboProvider(
        Credentials(CONTABO_VALUES), auth_url=mock_contabo.auth_url, timeout=2.0
    )


def remove(
    provider: ContaboProvider, configuration: ProviderConfiguration, identifier: str
) -> Operation:
    server = provider.get_server(configuration, identifier)
    return provider.request_removal(configuration, server)


def follow(
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
    operation: Operation,
) -> Operation:
    for _ in range(MAX_POLLS):
        operation = provider.operation_state(configuration, operation)
        if operation.has_ended:
            break
    return operation


def test_the_kind_is_cancel(
    provider: ContaboProvider, configuration: ProviderConfiguration
) -> None:
    assert provider.removal_kind(configuration) is RemovalKind.CANCEL


def test_the_cancellation_posts_to_the_cancel_path_with_token_and_request_id(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    remove(provider, configuration, "101")

    (cancel,) = [r for r in mock_contabo.write_requests]
    assert (cancel.method, cancel.path) == ("POST", "/v1/compute/instances/101/cancel")
    assert cancel.request_id is not None
    assert mock_contabo.state.cancelled == ["101"]


def test_the_running_operation_holds_the_date_the_answer_reports(
    provider: ContaboProvider, configuration: ProviderConfiguration
) -> None:
    operation = remove(provider, configuration, "101")

    assert operation.state is OperationState.RUNNING
    assert operation.action is PowerAction.REMOVE
    assert operation.server_identifier == "101"
    assert operation.provider_reference is None
    assert operation.detail == f"cancellation requested, effective {CANCEL_DATE}"


def test_the_succeeded_operation_holds_the_effective_date_as_reported(
    provider: ContaboProvider, configuration: ProviderConfiguration
) -> None:
    final = follow(provider, configuration, remove(provider, configuration, "101"))

    assert final.state is OperationState.SUCCEEDED
    assert final.detail == f"cancelled, effective {CANCEL_DATE}"


def test_status_cancelled_without_a_date_is_still_success_and_says_so(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_contabo.state.cancel_reports_no_date = True

    started = remove(provider, configuration, "101")
    final = follow(provider, configuration, started)

    assert started.detail == "cancellation requested, no date reported"
    assert final.state is OperationState.SUCCEEDED
    assert final.detail == "cancelled, no date was reported"


@pytest.mark.parametrize("status", ["running", "stopped", "provisioning", "other"])
def test_a_status_that_is_not_cancelled_is_not_success(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
    status: str,
) -> None:
    operation = remove(provider, configuration, "101")
    mock_contabo.state.transitions.clear()
    mock_contabo.state.instances[1]["status"] = status

    after = provider.operation_state(configuration, operation)

    assert after.state is OperationState.RUNNING
    assert after.finished_at is None


def test_a_status_error_fails_the_operation_naming_the_status(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_contabo.state.transition_fails = True

    final = follow(provider, configuration, remove(provider, configuration, "101"))

    assert final.state is OperationState.FAILED
    assert final.detail is not None
    assert "error" in final.detail


def test_a_running_instance_is_not_blocked_because_contabo_cancels_it_as_it_is(
    provider: ContaboProvider, configuration: ProviderConfiguration
) -> None:
    running = provider.get_server(configuration, "100")

    assert running.state == "running"
    assert provider.removal_block(configuration, running) is None


def test_a_cancelled_instance_is_already_removed_and_no_request_is_made(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    cancelled = Server("101", "db", "cancelled", "", (), False)

    block = provider.removal_block(configuration, cancelled)

    assert block is RemovalBlock.ALREADY_REMOVED
    assert mock_contabo.state.requests == []


def test_the_consequence_says_the_contract_runs_on_and_cannot_be_withdrawn(
    provider: ContaboProvider, configuration: ProviderConfiguration
) -> None:
    server = Server("101", "db", "stopped", "", (), False)

    text = provider.consequence(configuration, server)

    assert "cancellation of instance 101" in text
    assert "contract and its billing continue" in text
    assert "effective date" in text
    assert "cannot withdraw" in text


def test_the_reads_before_the_request_send_nothing_at_all(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    server = Server("101", "db", "stopped", "", (), False)

    provider.removal_kind(configuration)
    provider.removal_block(configuration, server)
    provider.consequence(configuration, server)

    assert mock_contabo.state.requests == []


def test_an_unknown_instance_is_not_found_and_changes_nothing(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    ghost = Server("999", "ghost", "stopped", "", (), False)

    with pytest.raises(ServerNotFoundError):
        provider.request_removal(configuration, ghost)

    assert mock_contabo.state.cancelled == []


def test_a_non_numeric_identifier_is_not_found_and_sends_nothing(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    odd = Server("../x", "odd", "stopped", "", (), False)

    with pytest.raises(ServerNotFoundError):
        provider.request_removal(configuration, odd)

    assert mock_contabo.write_requests == []


@pytest.mark.parametrize("status", [400, 403, 409, 429])
def test_a_rejection_of_the_cancel_is_a_plain_failure_and_changes_nothing(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
    status: int,
) -> None:
    mock_contabo.state.cancel_forced_status = status

    operation = remove(provider, configuration, "101")

    assert operation.state is OperationState.FAILED
    assert operation.detail
    assert mock_contabo.state.cancelled == []


@pytest.mark.parametrize(
    "error",
    [
        ProviderTimeoutError("the provider did not answer within 2.0 seconds"),
        ProviderServiceError("Contabo answered 503: service error"),
    ],
)
def test_a_timeout_or_a_server_error_on_the_cancel_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
    error: ProviderError,
) -> None:
    server = Server("101", "db", "stopped", "", (), False)

    def fail(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(provider, "_authorised_post", fail)

    operation = provider.request_removal(configuration, server)

    assert operation.state is OperationState.UNKNOWN
    assert operation.server_identifier == "101"
    assert operation.detail is not None
    assert "may have reached the provider" in operation.detail


def test_a_server_error_answer_is_unknown_because_the_cancel_may_be_recorded(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_contabo.state.cancel_forced_status = 500

    operation = remove(provider, configuration, "101")

    assert operation.state is OperationState.UNKNOWN
    assert operation.detail is not None
    assert "service error" in operation.detail


def test_a_rate_limit_on_the_cancel_is_a_plain_failure(
    monkeypatch: pytest.MonkeyPatch,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    server = Server("101", "db", "stopped", "", (), False)

    def limited(*args: object, **kwargs: object) -> object:
        raise ProviderRateLimitError("Contabo answered 429")

    monkeypatch.setattr(provider, "_authorised_post", limited)

    assert (
        provider.request_removal(configuration, server).state is OperationState.FAILED
    )


def test_a_token_that_keeps_being_rejected_is_renewed_once_and_nothing_is_cancelled(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    server = provider.get_server(configuration, "101")
    mock_contabo.state.token_expires_after = 0

    operation = provider.request_removal(configuration, server)

    # A 401 means nothing was processed, so one renewal and one repeat are safe.
    assert operation.state is OperationState.FAILED
    assert mock_contabo.state.cancelled == []
    assert len(mock_contabo.token_requests) == 2
