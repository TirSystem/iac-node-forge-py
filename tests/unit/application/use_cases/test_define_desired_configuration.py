"""UC-003: define, re-check and list desired configurations with fakes."""

import pytest

from iac_node_forge.application.errors import (
    ConfigurationNotFoundError,
    DesiredConfigurationNotFoundError,
    ProviderUnreachableError,
)
from iac_node_forge.application.use_cases import (
    DefineDesiredConfiguration,
    DesiredConfigurationValidator,
)
from iac_node_forge.domain.provider_value import ProviderValue
from tests.unit.application.fakes import (
    FailingProvider,
    FakeConfigurationStore,
    FakeDesiredConfigurationStore,
    FakeProviderResolver,
    FakeProvisionProvider,
)
from tests.unit.application.samples import LAB, LAB_ID
from tests.unit.application.use_cases.provisioning_samples import (
    GOOD_VALUES,
    IMAGE,
    desired,
    provider,
)

VALUES = {v.name: v.value for v in GOOD_VALUES}


def build(
    fake: FakeProvisionProvider | None = None,
) -> tuple[DefineDesiredConfiguration, FakeDesiredConfigurationStore]:
    configurations = FakeConfigurationStore()
    configurations.save(LAB)
    store = FakeDesiredConfigurationStore()
    validator = DesiredConfigurationValidator(FakeProviderResolver(fake or provider()))
    return DefineDesiredConfiguration(validator, configurations, store), store


def test_a_valid_description_is_saved_under_its_name() -> None:
    define, store = build()

    view = define.define_desired_configuration(
        "Lab", "small", IMAGE, ["deploy"], "boot", VALUES
    )

    assert view.valid
    assert view.problems == ()
    assert view.name == "small"
    assert store.saved[LAB_ID, "small"] == desired()


def test_a_description_with_an_unsupported_choice_is_reported_and_not_saved() -> None:
    define, store = build()

    view = define.define_desired_configuration(
        "Lab", "small", IMAGE, [], None, {**VALUES, "node": "pve9"}
    )

    assert not view.valid
    assert len(view.problems) == 1
    assert "'pve9'" in view.problems[0]
    assert store.saves == 0


def test_the_same_name_replaces_the_description() -> None:
    define, store = build()
    define.define_desired_configuration("Lab", "small", IMAGE, [], None, VALUES)

    define.define_desired_configuration(
        "Lab", "small", IMAGE, [], None, {**VALUES, "cores": "4"}
    )

    assert store.saves == 2
    assert len(store.load_all(LAB_ID)) == 1
    assert store.saved[LAB_ID, "small"].value("cores") == "4"


def test_a_failed_redefinition_keeps_the_saved_description() -> None:
    define, store = build()
    define.define_desired_configuration("Lab", "small", IMAGE, [], None, VALUES)

    view = define.define_desired_configuration(
        "Lab", "small", IMAGE, [], None, {**VALUES, "cores": "99"}
    )

    assert not view.valid
    assert store.saved[LAB_ID, "small"].value("cores") == "2"


def test_a_provider_without_the_capability_is_reported_as_unsupported() -> None:
    define, store = build(provider(supported=False))

    view = define.define_desired_configuration("Lab", "small", IMAGE, [], None, VALUES)

    assert not view.valid
    assert "provision_server" in view.problems[0]
    assert store.saves == 0


@pytest.mark.parametrize(
    ("name", "hint"), [("", None), ("small", "x" * 501), ("small", "  ")]
)
def test_a_description_the_domain_refuses_is_invalid_and_not_saved(
    name: str, hint: str | None
) -> None:
    define, store = build()

    view = define.define_desired_configuration("Lab", name, IMAGE, [], hint, VALUES)

    assert not view.valid
    assert view.problems
    assert store.saves == 0


def test_a_repeated_key_name_is_kept_once_in_order() -> None:
    define, store = build()

    view = define.define_desired_configuration(
        "Lab", "small", IMAGE, ["b", "a", "b"], None, VALUES
    )

    assert view.access_key_names == ("b", "a")
    assert store.saved[LAB_ID, "small"].access_key_names == ("b", "a")


def test_defining_sends_no_write_to_the_provider() -> None:
    fake = provider()
    define, _ = build(fake)

    define.define_desired_configuration("Lab", "small", IMAGE, [], None, VALUES)

    assert fake.requests == []
    assert set(fake.reads) <= {"value_rules"}


def test_an_unknown_configuration_is_an_error() -> None:
    define, _ = build()

    with pytest.raises(ConfigurationNotFoundError):
        define.define_desired_configuration("Nope", "small", IMAGE, [], None, VALUES)


def test_a_provider_that_cannot_be_read_saves_nothing() -> None:
    configurations = FakeConfigurationStore()
    configurations.save(LAB)
    store = FakeDesiredConfigurationStore()
    resolver = FakeProviderResolver(
        FailingProvider(ProviderUnreachableError("down")), provision=provider()
    )
    define = DefineDesiredConfiguration(
        DesiredConfigurationValidator(resolver), configurations, store
    )

    with pytest.raises(ProviderUnreachableError):
        define.define_desired_configuration("Lab", "small", IMAGE, [], None, VALUES)

    assert store.saves == 0


def test_a_saved_description_is_checked_again_without_changing_it() -> None:
    define, store = build()
    store.save(desired(values=(*GOOD_VALUES, ProviderValue("colour", "red"))))
    saves = store.saves

    view = define.validate_desired_configuration("Lab", "small")

    assert not view.valid
    assert "colour" in view.problems[0]
    assert store.saves == saves


def test_validating_a_name_that_does_not_exist_is_not_found() -> None:
    define, _ = build()

    with pytest.raises(DesiredConfigurationNotFoundError):
        define.validate_desired_configuration("Lab", "missing")


def test_the_list_shows_the_saved_descriptions_of_the_configuration() -> None:
    define, store = build()
    store.save(desired("a"))
    store.save(desired("b", values=(ProviderValue("colour", "red"),)))

    listed = define.list_desired_configurations("Lab")
    checked = define.list_desired_configurations("Lab", check=True)

    assert [v.name for v in listed] == ["a", "b"]
    assert all(v.valid for v in listed)
    assert [v.valid for v in checked] == [True, False]
