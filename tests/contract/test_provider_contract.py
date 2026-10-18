"""Behaviours every ProviderPort implementation shares.

Each test runs against FakeProvider, ProxmoxProvider plus its mock and
ContaboProvider plus its mock, which shows that the fake and both adapters
agree. No variant is skipped.
"""

import logging

import pytest

from iac_node_forge.application.capabilities import PROVISION_SERVER
from iac_node_forge.application.errors import (
    ServerNotFoundError,
    UnknownOptionKindError,
)
from iac_node_forge.application.ports import ProviderPort
from iac_node_forge.domain.capability import Capability
from tests.contract.conftest import ALL_CAPABILITIES
from tests.contract.provider_under_test import ProviderUnderTest


def test_provider_satisfies_provider_port(
    provider_under_test: ProviderUnderTest,
) -> None:
    assert isinstance(provider_under_test.provider, ProviderPort)


def test_validate_access_succeeds_with_valid_credentials(
    provider_under_test: ProviderUnderTest,
) -> None:
    result = provider_under_test.provider.validate_access(
        provider_under_test.configuration.address, provider_under_test.credentials
    )

    assert result.succeeded


def test_validate_access_fails_with_reason_for_wrong_credentials(
    provider_under_test: ProviderUnderTest,
) -> None:
    result = provider_under_test.provider.validate_access(
        provider_under_test.configuration.address,
        provider_under_test.wrong_credentials,
    )

    assert not result.succeeded
    assert result.reason
    assert not [v for v in provider_under_test.secret_values if v in result.reason]


def test_discover_capabilities_returns_read_and_power_set(
    provider_under_test: ProviderUnderTest,
) -> None:
    capabilities = provider_under_test.provider.discover_capabilities(
        provider_under_test.configuration
    )

    assert capabilities == ALL_CAPABILITIES


def test_discover_capabilities_declares_no_write_beyond_power_and_provision(
    provider_under_test: ProviderUnderTest,
) -> None:
    capabilities = provider_under_test.provider.discover_capabilities(
        provider_under_test.configuration
    )
    # The four power capabilities and provision_server are the only declared
    # writes (ADR-0006, ADR-0007).
    write_words = (
        "create",
        "update",
        "delete",
        "remove",
        "cancel",
        "reinstall",
        "rescue",
        "snapshot",
        "migrate",
    )

    assert capabilities
    assert not [
        c.name for c in capabilities if any(word in c.name for word in write_words)
    ]
    assert Capability(PROVISION_SERVER) in capabilities


def test_list_server_options_returns_options_of_each_kind(
    provider_under_test: ProviderUnderTest,
) -> None:
    assert provider_under_test.expected_options
    for kind, expected in provider_under_test.expected_options.items():
        options = provider_under_test.provider.list_server_options(
            provider_under_test.configuration, kind
        )

        assert options == expected


def test_list_server_options_raises_for_unknown_kind(
    provider_under_test: ProviderUnderTest,
) -> None:
    with pytest.raises(UnknownOptionKindError):
        provider_under_test.provider.list_server_options(
            provider_under_test.configuration, provider_under_test.unknown_option_kind
        )


def test_list_servers_maps_provider_data_to_servers(
    provider_under_test: ProviderUnderTest,
) -> None:
    servers = provider_under_test.provider.list_servers(
        provider_under_test.configuration
    )

    assert servers == provider_under_test.expected_servers


def test_get_server_returns_server_by_identifier(
    provider_under_test: ProviderUnderTest,
) -> None:
    expected = provider_under_test.expected_servers[1]

    server = provider_under_test.provider.get_server(
        provider_under_test.configuration, expected.server_identifier
    )

    assert server == expected


def test_get_server_raises_not_found_for_absent_identifier(
    provider_under_test: ProviderUnderTest,
) -> None:
    with pytest.raises(ServerNotFoundError):
        provider_under_test.provider.get_server(
            provider_under_test.configuration, provider_under_test.missing_identifier
        )


def test_secret_values_never_appear_in_reprs_errors_or_logs(
    provider_under_test: ProviderUnderTest, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    provider = provider_under_test.provider
    configuration = provider_under_test.configuration
    texts = [
        repr(provider),
        repr(provider_under_test.credentials),
        str(provider_under_test.credentials),
        repr(provider_under_test.wrong_credentials),
        provider.validate_access(
            configuration.address, provider_under_test.wrong_credentials
        ).reason,
    ]
    with pytest.raises(ServerNotFoundError) as missing:
        provider.get_server(configuration, provider_under_test.missing_identifier)
    texts.append(str(missing.value))
    with pytest.raises(UnknownOptionKindError) as unknown:
        provider.list_server_options(
            configuration, provider_under_test.unknown_option_kind
        )
    texts.append(str(unknown.value))
    texts.extend(record.getMessage() for record in caplog.records)

    leaked = [
        value
        for value in provider_under_test.secret_values
        if any(value in text for text in texts)
    ]
    assert texts
    assert not leaked
