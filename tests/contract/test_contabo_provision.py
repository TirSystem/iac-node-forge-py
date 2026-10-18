"""Provisioning behaviours specific to the Contabo adapter, against the mock API."""

import uuid
from dataclasses import replace

import pytest

from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.operation import Operation, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.provider_value import ProviderValue
from iac_node_forge.domain.value_rule import IMAGE_RULE_NAMES
from iac_node_forge.infrastructure.contabo import ContaboProvider
from iac_node_forge.infrastructure.contabo._provision import value_rules
from tests.contract.conftest import (
    CONTABO_DESIRED,
    CONTABO_VALUES,
    EXPECTED_IMAGES,
    EXPECTED_PRODUCTS,
    SERVER_NAME,
    contabo_configuration_for,
)
from tests.contract.mock_contabo import MockContaboServer


@pytest.fixture
def configuration(mock_contabo: MockContaboServer) -> ProviderConfiguration:
    return contabo_configuration_for(mock_contabo.address)


@pytest.fixture
def provider(mock_contabo: MockContaboServer) -> ContaboProvider:
    return ContaboProvider(
        Credentials(CONTABO_VALUES), auth_url=mock_contabo.auth_url, timeout=2.0
    )


def create(
    provider: ContaboProvider, configuration: ProviderConfiguration
) -> Operation:
    return provider.request_provision(configuration, CONTABO_DESIRED, SERVER_NAME, None)


def test_creation_posts_the_documented_body_with_a_numeric_period(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    create(provider, configuration)

    assert mock_contabo.state.created == [
        {
            "imageId": EXPECTED_IMAGES[0].value,
            "productId": "V45",
            "region": "EU1",
            "period": 12,
            "displayName": SERVER_NAME,
        }
    ]


def test_creation_carries_a_request_id_and_the_new_instance_id(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = create(provider, configuration)

    (post,) = [r for r in mock_contabo.write_requests if r.method == "POST"]
    assert post.request_id is not None
    assert uuid.UUID(post.request_id).version == 4
    assert operation.server_identifier == "102"
    assert operation.action is PowerAction.PROVISION
    assert operation.state is OperationState.RUNNING


def test_the_neutral_image_is_the_image_sent(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    other = EXPECTED_IMAGES[1].value

    provider.request_provision(
        configuration, replace(CONTABO_DESIRED, image=other), "a", None
    )

    assert [body["imageId"] for body in mock_contabo.state.created] == [other]
    assert "image" not in {v.name for v in CONTABO_DESIRED.values}


def test_the_neutral_image_is_a_rule_and_not_a_value() -> None:
    names = {rule.name for rule in value_rules()}

    assert len(names & set(IMAGE_RULE_NAMES)) == 1
    assert "image" in names


@pytest.mark.parametrize(
    ("period", "text"),
    [("1", "1 month"), ("12", "12 months"), ("24", "24 months")],
)
def test_the_cost_names_the_period_and_the_commitment(
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
    period: str,
    text: str,
) -> None:
    desired = replace(
        CONTABO_DESIRED,
        values=tuple(
            ProviderValue(v.name, period) if v.name == "period" else v
            for v in CONTABO_DESIRED.values
        ),
    )

    cost = provider.cost_consequence(configuration, desired)

    assert cost is not None
    assert f"contract period of {text}" in cost
    assert "commits the account" in cost


def test_the_value_rules_offer_the_three_periods(
    provider: ContaboProvider, configuration: ProviderConfiguration
) -> None:
    (period,) = [r for r in provider.value_rules(configuration) if r.name == "period"]

    assert period.allowed == ("1", "12", "24")
    assert period.required


def test_no_identifier_is_reserved_and_no_request_is_sent_for_it(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    assert provider.reserve_identifier(configuration) is None
    assert mock_contabo.api_requests == []


def test_product_options_are_read_from_the_product_listing(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    options = provider.list_server_options(configuration, "product")

    assert options == EXPECTED_PRODUCTS
    assert {r.path for r in mock_contabo.api_requests} == {"/v1/compute/products"}


@pytest.mark.parametrize("status", ["provisioning", "installing", "unknown", "other"])
def test_a_status_other_than_running_or_error_is_not_final(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
    status: str,
) -> None:
    operation = create(provider, configuration)
    mock_contabo.state.transitions.clear()
    mock_contabo.state.instances[-1]["status"] = status

    state = provider.operation_state(configuration, operation)

    assert state == operation
    assert not state.has_ended


def test_status_running_succeeds_and_status_error_fails_with_the_status(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = create(provider, configuration)
    mock_contabo.state.transitions.clear()

    mock_contabo.state.instances[-1]["status"] = "running"
    succeeded = provider.operation_state(configuration, operation)
    mock_contabo.state.instances[-1]["status"] = "error"
    failed = provider.operation_state(configuration, operation)

    assert succeeded.state is OperationState.SUCCEEDED
    assert failed.state is OperationState.FAILED
    assert failed.detail is not None
    assert "error" in failed.detail


def test_an_accepted_request_that_names_no_instance_stays_running_and_says_so(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_contabo.state.create_answers_without_instance = True

    operation = create(provider, configuration)

    assert operation.state is OperationState.RUNNING
    assert operation.server_identifier is None
    assert operation.detail is not None
    assert "named no instance" in operation.detail
    assert provider.operation_state(configuration, operation) == operation
    assert len(mock_contabo.write_requests) == 1


@pytest.mark.parametrize(
    ("status", "words"),
    [(429, "rate limit"), (500, "service error"), (403, "not permitted")],
)
def test_a_provider_refusal_gives_a_failed_operation_without_a_server(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
    status: int,
    words: str,
) -> None:
    mock_contabo.state.create_forced_status = status

    operation = create(provider, configuration)

    assert operation.state is OperationState.FAILED
    assert operation.server_identifier is None
    assert operation.detail is not None
    assert words in operation.detail
    assert len(mock_contabo.state.instances) == 2


@pytest.mark.parametrize("missing", ["region", "product", "period"])
def test_a_missing_value_gives_a_failed_operation_and_sends_nothing(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
    missing: str,
) -> None:
    desired = replace(
        CONTABO_DESIRED,
        values=tuple(v for v in CONTABO_DESIRED.values if v.name != missing),
    )

    operation = provider.request_provision(configuration, desired, SERVER_NAME, None)

    assert operation.state is OperationState.FAILED
    assert operation.detail is not None
    assert missing in operation.detail
    assert mock_contabo.write_requests == []


def test_a_period_outside_the_three_gives_a_failed_operation_and_sends_nothing(
    mock_contabo: MockContaboServer,
    provider: ContaboProvider,
    configuration: ProviderConfiguration,
) -> None:
    desired = replace(
        CONTABO_DESIRED,
        values=tuple(
            ProviderValue("period", "6") if v.name == "period" else v
            for v in CONTABO_DESIRED.values
        ),
    )

    operation = provider.request_provision(configuration, desired, SERVER_NAME, None)

    assert operation.state is OperationState.FAILED
    assert mock_contabo.write_requests == []
