"""Behaviours every ProviderPort implementation shares.

Each test runs against FakeProvider and against ProxmoxProvider plus the mock,
which shows that the fake and the adapter agree.
"""

import pytest

from iac_node_forge.application.errors import (
    ServerNotFoundError,
    UnknownOptionKindError,
)
from iac_node_forge.domain.credentials import Credentials
from tests.contract.conftest import (
    EXPECTED_NODES,
    EXPECTED_SERVERS,
    EXPECTED_STORAGE,
    READ_ONLY_CAPABILITIES,
    ProviderCase,
)


def test_validate_access_succeeds_with_valid_token(provider_case: ProviderCase) -> None:
    result = provider_case.provider.validate_access(
        provider_case.configuration.address, Credentials.single(provider_case.token)
    )

    assert result.succeeded


def test_validate_access_fails_with_reason_for_wrong_token(
    provider_case: ProviderCase,
) -> None:
    result = provider_case.provider.validate_access(
        provider_case.configuration.address,
        Credentials.single(provider_case.wrong_token),
    )

    assert not result.succeeded
    assert result.reason
    assert provider_case.wrong_token not in result.reason


def test_discover_capabilities_returns_read_only_set(
    provider_case: ProviderCase,
) -> None:
    capabilities = provider_case.provider.discover_capabilities(
        provider_case.configuration
    )

    assert capabilities == READ_ONLY_CAPABILITIES


def test_discover_capabilities_declares_no_write_capability(
    provider_case: ProviderCase,
) -> None:
    capabilities = provider_case.provider.discover_capabilities(
        provider_case.configuration
    )
    write_words = ("create", "update", "delete", "remove", "power", "snapshot")

    assert capabilities
    assert not [
        c.name for c in capabilities if any(word in c.name for word in write_words)
    ]


def test_list_server_options_returns_nodes(provider_case: ProviderCase) -> None:
    options = provider_case.provider.list_server_options(
        provider_case.configuration, "node"
    )

    assert options == EXPECTED_NODES


def test_list_server_options_returns_storage_of_every_node(
    provider_case: ProviderCase,
) -> None:
    options = provider_case.provider.list_server_options(
        provider_case.configuration, "storage"
    )

    assert options == EXPECTED_STORAGE


def test_list_server_options_raises_for_unknown_kind(
    provider_case: ProviderCase,
) -> None:
    with pytest.raises(UnknownOptionKindError):
        provider_case.provider.list_server_options(
            provider_case.configuration, "flavour"
        )


def test_list_servers_maps_resources_to_servers(provider_case: ProviderCase) -> None:
    servers = provider_case.provider.list_servers(provider_case.configuration)

    assert servers == EXPECTED_SERVERS


def test_get_server_returns_server_by_identifier(provider_case: ProviderCase) -> None:
    server = provider_case.provider.get_server(provider_case.configuration, "101")

    assert server == EXPECTED_SERVERS[1]


def test_get_server_raises_not_found_for_absent_identifier(
    provider_case: ProviderCase,
) -> None:
    with pytest.raises(ServerNotFoundError):
        provider_case.provider.get_server(provider_case.configuration, "999")
