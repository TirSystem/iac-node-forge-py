"""DesiredConfiguration, ProviderValue, ValueRule and ProvisionIntent (ADR-0007)."""

from datetime import UTC, datetime

import pytest

from iac_node_forge.domain.desired_configuration import (
    MAX_BOOTSTRAP_HINT_LENGTH,
    DesiredConfiguration,
)
from iac_node_forge.domain.errors import (
    BootstrapHintTooLongError,
    DuplicateValueNameError,
    EmptyValueError,
    InvalidValueRuleError,
    NaiveTimestampError,
)
from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.provider_value import ProviderValue
from iac_node_forge.domain.provision_intent import (
    IntentId,
    IntentState,
    ProvisionIntent,
)
from iac_node_forge.domain.value_rule import ValueRule

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def desired(**overrides: object) -> DesiredConfiguration:
    values: dict[str, object] = {
        "configuration_id": ConfigurationId("cfg-1"),
        "desired_name": "web",
        "image": "alpine",
        "access_key_names": ("admin-key",),
        "values": (ProviderValue("node", "pve1"), ProviderValue("cores", "2")),
    }
    values.update(overrides)
    return DesiredConfiguration(**values)  # type: ignore[arg-type]


def intent(**overrides: object) -> ProvisionIntent:
    values: dict[str, object] = {
        "intent_id": IntentId("int-1"),
        "configuration_id": ConfigurationId("cfg-1"),
        "desired_name": "web",
        "server_name": "web-1",
        "image": "alpine",
        "values": (ProviderValue("node", "pve1"),),
        "state": IntentState.PENDING,
        "recorded_at": NOW,
    }
    values.update(overrides)
    return ProvisionIntent(**values)  # type: ignore[arg-type]


def test_desired_configuration_finds_a_value_by_name() -> None:
    config = desired()

    assert config.value("node") == "pve1"
    assert config.value("storage") is None


@pytest.mark.parametrize("field_name", ["configuration_id", "desired_name", "image"])
def test_desired_configuration_rejects_a_blank_core_field(field_name: str) -> None:
    with pytest.raises(EmptyValueError):
        desired(**{field_name: " "})


def test_desired_configuration_accepts_a_hint_of_exactly_the_limit() -> None:
    hint = "x" * MAX_BOOTSTRAP_HINT_LENGTH

    assert desired(bootstrap_hint=hint).bootstrap_hint == hint


def test_desired_configuration_rejects_a_hint_over_the_limit() -> None:
    with pytest.raises(BootstrapHintTooLongError):
        desired(bootstrap_hint="x" * (MAX_BOOTSTRAP_HINT_LENGTH + 1))


def test_desired_configuration_rejects_two_values_with_one_name() -> None:
    with pytest.raises(DuplicateValueNameError):
        desired(values=(ProviderValue("node", "a"), ProviderValue("node", "b")))


def test_desired_configuration_rejects_a_blank_access_key_name() -> None:
    with pytest.raises(EmptyValueError):
        desired(access_key_names=("",))


@pytest.mark.parametrize(("name", "value"), [("", "x"), ("node", " ")])
def test_provider_value_rejects_blank_text(name: str, value: str) -> None:
    with pytest.raises(EmptyValueError):
        ProviderValue(name, value)


def test_value_rule_rejects_a_minimum_above_its_maximum() -> None:
    with pytest.raises(InvalidValueRuleError):
        ValueRule("cores", required=True, minimum=5, maximum=1)


def test_value_rule_defaults_to_no_check_data() -> None:
    rule = ValueRule("kind", required=False)

    assert rule.option_kind is None
    assert rule.allowed == ()
    assert rule.minimum is None
    assert rule.maximum is None


def test_intent_is_pending_without_an_operation_until_the_request_is_made() -> None:
    pending = intent()

    assert pending.state is IntentState.PENDING
    assert pending.operation_id is None
    assert pending.reserved_identifier is None


def test_intent_keeps_the_reserved_identifier_and_the_operation_link() -> None:
    linked = intent(reserved_identifier="102", operation_id=OperationId("op-1"))

    assert linked.reserved_identifier == "102"
    assert linked.operation_id == "op-1"


def test_intent_rejects_a_time_without_a_timezone() -> None:
    with pytest.raises(NaiveTimestampError):
        intent(recorded_at=datetime(2026, 10, 18, 12, 0))


@pytest.mark.parametrize("field_name", ["intent_id", "server_name", "image"])
def test_intent_rejects_a_blank_text_field(field_name: str) -> None:
    with pytest.raises(EmptyValueError):
        intent(**{field_name: ""})
