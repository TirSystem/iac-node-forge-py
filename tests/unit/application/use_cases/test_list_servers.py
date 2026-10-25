"""UC-007: listing and inspecting servers through the fakes."""

import pytest

from iac_node_forge.application.dto import FailureCategory
from iac_node_forge.application.errors import (
    CredentialNotFoundError,
    NoConfigurationError,
    ProviderAuthenticationError,
    ProviderUnreachableError,
    ServerNotFoundError,
)
from iac_node_forge.application.use_cases import ListServers
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from tests.unit.application.fakes import (
    FailingProvider,
    FakeBackupProvider,
    FakeConfigurationStore,
    FakePowerProvider,
    FakeProvider,
    FakeProviderResolver,
    FakeProvisionProvider,
    FakeRemovalProvider,
)
from tests.unit.application.samples import (
    CACHE,
    DB,
    EDGE,
    EDGE_ID,
    LAB,
    LAB_ID,
    WEB,
    WEB_IS_MANAGED,
)


def make(
    providers: FakeProviderResolver, *configurations: ProviderConfiguration
) -> ListServers:
    store = FakeConfigurationStore(managed=(WEB_IS_MANAGED,))
    for configuration in configurations:
        store.save(configuration)
    return ListServers(providers, store)


def test_list_servers_marks_recorded_servers_managed_and_others_not() -> None:
    resolver = FakeProviderResolver(FakeProvider(servers=(WEB, DB)))

    listing = make(resolver, LAB).list_servers()

    managed = {server.server_identifier: server.managed for server in listing.servers}
    assert managed == {"100": True, "101": False}
    assert listing.failures == ()


def test_list_servers_covers_every_configuration() -> None:
    resolver = FakeProviderResolver(
        FakeProvider(servers=(WEB,)),
        {EDGE_ID: FakeProvider(servers=(CACHE,))},
    )

    listing = make(resolver, LAB, EDGE).list_servers()

    assert [(s.provider_name, s.server_identifier) for s in listing.servers] == [
        ("Lab", "100"),
        ("Edge", "200"),
    ]


def test_list_servers_shows_provider_state_size_and_addresses() -> None:
    resolver = FakeProviderResolver(FakeProvider(servers=(WEB,)))

    (server,) = make(resolver, LAB).list_servers().servers

    assert server.provider_name == "Lab"
    assert server.state == "running"
    assert server.size == "2 vCPU, 2048 MiB"
    assert server.addresses == ("10.0.0.5",)


def test_list_servers_reports_unreachable_provider_and_keeps_the_others() -> None:
    resolver = FakeProviderResolver(
        FakeProvider(servers=(WEB,)),
        {EDGE_ID: FailingProvider(ProviderUnreachableError("no route"))},
    )

    listing = make(resolver, LAB, EDGE).list_servers()

    assert [s.server_identifier for s in listing.servers] == ["100"]
    (failure,) = listing.failures
    assert failure.provider_name == "Edge"
    assert failure.configuration_id == EDGE_ID
    assert failure.category is FailureCategory.UNREACHABLE
    assert failure.reason == "no route"


def test_list_servers_reports_a_rejected_token_as_a_failure() -> None:
    resolver = FakeProviderResolver(FailingProvider(ProviderAuthenticationError("401")))

    (failure,) = make(resolver, LAB).list_servers().failures

    assert failure.category is FailureCategory.REJECTED


def test_list_servers_reports_unobtainable_credentials_as_a_failure() -> None:
    class NoCredentials:
        def provider_for(self, configuration: ProviderConfiguration) -> FakeProvider:
            raise CredentialNotFoundError("gone")

        def power_for(self, configuration: ProviderConfiguration) -> FakePowerProvider:
            raise CredentialNotFoundError("gone")

        def provision_for(
            self, configuration: ProviderConfiguration
        ) -> FakeProvisionProvider:
            raise CredentialNotFoundError("gone")

        def removal_for(
            self, configuration: ProviderConfiguration
        ) -> FakeRemovalProvider:
            raise CredentialNotFoundError("gone")

        def backup_for(
            self, configuration: ProviderConfiguration
        ) -> FakeBackupProvider:
            raise CredentialNotFoundError("gone")

    store = FakeConfigurationStore()
    store.save(LAB)
    use_case = ListServers(NoCredentials(), store)

    (failure,) = use_case.list_servers().failures

    assert failure.category is FailureCategory.CREDENTIALS


def test_list_servers_can_be_limited_to_one_configuration() -> None:
    resolver = FakeProviderResolver(FakeProvider(servers=(WEB,)))

    listing = make(resolver, LAB, EDGE).list_servers("Edge")

    assert resolver.resolved == [EDGE_ID]
    assert [s.provider_name for s in listing.servers] == ["Edge"]


def test_list_servers_raises_when_nothing_is_configured() -> None:
    with pytest.raises(NoConfigurationError):
        make(FakeProviderResolver(FakeProvider())).list_servers()


def test_inspect_server_returns_the_server_with_managed_marking() -> None:
    resolver = FakeProviderResolver(FakeProvider(servers=(WEB, DB)))

    server = make(resolver, LAB).inspect_server("Lab", "100")

    assert server.name == "web"
    assert server.configuration_id == LAB_ID
    assert server.managed


def test_inspect_server_raises_not_found_for_an_unknown_identifier() -> None:
    resolver = FakeProviderResolver(FakeProvider(servers=(WEB,)))

    with pytest.raises(ServerNotFoundError):
        make(resolver, LAB).inspect_server("Lab", "999")
