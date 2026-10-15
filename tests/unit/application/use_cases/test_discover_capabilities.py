"""UC-002: capabilities and server options through the fakes."""

import pytest

from iac_node_forge.application.errors import (
    ConfigurationNotFoundError,
    NoConfigurationError,
    ProviderUnreachableError,
)
from iac_node_forge.application.use_cases import DiscoverCapabilities
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.server_option import ServerOption
from tests.unit.application.fakes import (
    FailingProvider,
    FakeConfigurationStore,
    FakeProvider,
    FakeProviderResolver,
)
from tests.unit.application.samples import LAB, LAB_ID

PROVIDER = FakeProvider(
    capabilities=frozenset({Capability("list_servers"), Capability("inspect_server")}),
    options=(ServerOption("node", "pve1"), ServerOption("storage", "pve1/local")),
)


def make(provider: FakeProvider = PROVIDER) -> DiscoverCapabilities:
    store = FakeConfigurationStore()
    store.save(LAB)
    return DiscoverCapabilities(FakeProviderResolver(provider), store)


def test_capabilities_mark_declared_actions_supported_and_others_unsupported() -> None:
    report = make().discover_capabilities("Lab")

    status = {entry.name: entry.supported for entry in report.entries}
    assert status["list_servers"] is True
    assert status["inspect_server"] is True
    assert status["start_server"] is False
    assert report.configuration_id == LAB_ID


def test_capabilities_list_an_unknown_declared_action_as_supported() -> None:
    provider = FakeProvider(capabilities=frozenset({Capability("snapshot_server")}))

    report = make(provider).discover_capabilities("Lab")

    assert report.entries[-1].name == "snapshot_server"
    assert report.entries[-1].supported


def test_capabilities_find_the_configuration_by_identifier() -> None:
    report = make().discover_capabilities(LAB_ID)

    assert report.display_name == "Lab"


def test_capabilities_raise_not_found_for_an_unknown_configuration() -> None:
    with pytest.raises(ConfigurationNotFoundError):
        make().discover_capabilities("Nope")


def test_capabilities_raise_when_nothing_is_configured() -> None:
    use_case = DiscoverCapabilities(
        FakeProviderResolver(PROVIDER), FakeConfigurationStore()
    )

    with pytest.raises(NoConfigurationError):
        use_case.discover_capabilities("Lab")


def test_capabilities_propagate_an_unreachable_provider() -> None:
    provider = FailingProvider(ProviderUnreachableError("no route"))

    with pytest.raises(ProviderUnreachableError):
        make(provider).discover_capabilities("Lab")


def test_options_return_only_the_requested_kind() -> None:
    options = make().list_server_options("Lab", "node")

    assert [(o.kind, o.value) for o in options] == [("node", "pve1")]
