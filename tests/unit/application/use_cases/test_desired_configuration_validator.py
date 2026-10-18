"""The check of a desired configuration against rules and discovered options."""

from dataclasses import replace

import pytest

from iac_node_forge.application.errors import ProviderUnreachableError
from iac_node_forge.application.use_cases import DesiredConfigurationValidator
from iac_node_forge.domain.provider_value import ProviderValue
from iac_node_forge.domain.server_option import ServerOption
from iac_node_forge.domain.value_rule import ValueRule
from tests.unit.application.fakes import (
    FailingProvider,
    FakeProviderResolver,
    FakeProvisionProvider,
)
from tests.unit.application.samples import LAB
from tests.unit.application.use_cases.provisioning_samples import (
    GOOD_VALUES,
    desired,
    provider,
)


def validator() -> DesiredConfigurationValidator:
    return DesiredConfigurationValidator(FakeProviderResolver(provider()))


def with_value(name: str, value: str) -> tuple[ProviderValue, ...]:
    kept = tuple(v for v in GOOD_VALUES if v.name != name)
    return (*kept, ProviderValue(name, value))


def test_a_description_that_follows_the_rules_has_no_problem() -> None:
    assert validator().check(LAB, desired()) == []


def test_the_neutral_image_is_checked_against_the_option_kind_of_the_image_rule() -> (
    None
):
    problems = validator().check(LAB, desired(image="pve1/local:vztmpl/nope"))

    assert len(problems) == 1
    assert "image" in problems[0]
    assert "'pve1/local:vztmpl/nope'" in problems[0]
    assert "pve1/local:vztmpl/alpine.tar.xz" in problems[0]


def test_the_image_is_not_required_a_second_time_as_a_value() -> None:
    assert not any(v.name == "template" for v in desired().values)
    assert validator().check(LAB, desired()) == []


def test_a_value_named_like_the_image_rule_is_reported() -> None:
    values = (*GOOD_VALUES, ProviderValue("template", "pve1/local:vztmpl/x"))

    problems = validator().check(LAB, desired(values=values))

    assert len(problems) == 1
    assert "'template'" in problems[0]
    assert "image" in problems[0]


def test_a_value_that_is_not_among_the_options_is_reported_with_the_choices() -> None:
    problems = validator().check(LAB, desired(values=with_value("node", "pve9")))

    assert len(problems) == 1
    assert "'pve9'" in problems[0]
    assert "pve1, pve2" in problems[0]


def test_a_value_that_is_not_allowed_is_reported_with_the_allowed_values() -> None:
    problems = validator().check(LAB, desired(values=with_value("kind", "vz")))

    assert len(problems) == 1
    assert "'vz'" in problems[0]
    assert "lxc, qemu" in problems[0]


@pytest.mark.parametrize(
    ("value", "fragment"),
    [("0", "minimum is 1"), ("9", "maximum is 8"), ("two", "whole number")],
)
def test_a_number_outside_its_bounds_is_reported(value: str, fragment: str) -> None:
    problems = validator().check(LAB, desired(values=with_value("cores", value)))

    assert len(problems) == 1
    assert fragment in problems[0]


def test_a_missing_required_value_is_reported() -> None:
    values = tuple(v for v in GOOD_VALUES if v.name != "cores")

    problems = validator().check(LAB, desired(values=values))

    assert problems == ["the value 'cores' is required"]


def test_an_optional_value_may_be_left_out_and_is_checked_when_given() -> None:
    assert validator().check(LAB, desired()) == []
    problems = validator().check(LAB, desired(values=with_value("pool", "prod")))

    assert len(problems) == 1
    assert "'prod'" in problems[0]


def test_an_unknown_value_name_is_reported() -> None:
    problems = validator().check(LAB, desired(values=with_value("colour", "red")))

    assert len(problems) == 1
    assert "unknown value 'colour'" in problems[0]
    assert "cores" in problems[0]


def test_every_problem_is_reported_together() -> None:
    values = (ProviderValue("node", "pve9"), ProviderValue("colour", "red"))

    problems = validator().check(LAB, desired(image="nope", values=values))

    assert len(problems) == 5


def test_a_long_list_of_choices_is_cut() -> None:
    fake = FakeProvisionProvider(
        options=tuple(ServerOption("node", f"n{i}") for i in range(30)),
        rules=(ValueRule("node", required=True, option_kind="node"),),
    )

    problems = DesiredConfigurationValidator(FakeProviderResolver(fake)).check(
        LAB, replace(desired(), values=(ProviderValue("node", "pve9"),))
    )

    assert problems[0].endswith("n9, ...")
    assert "n10" not in problems[0]


def test_a_provider_that_cannot_be_read_raises_its_error() -> None:
    failing = FailingProvider(ProviderUnreachableError("down"))
    resolver = FakeProviderResolver(failing, provision=provider())

    with pytest.raises(ProviderUnreachableError):
        DesiredConfigurationValidator(resolver).check(LAB, desired())


def test_the_capability_is_declared_only_by_a_provider_that_can_create() -> None:
    assert validator().supports_provisioning(LAB)
    unsupported = DesiredConfigurationValidator(
        FakeProviderResolver(provider(supported=False))
    )
    assert not unsupported.supports_provisioning(LAB)
