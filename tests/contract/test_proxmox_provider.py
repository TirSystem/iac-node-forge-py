"""Behaviours specific to the Proxmox VE adapter, against the mock API."""

import logging
import socket

import pytest

from iac_node_forge.application.errors import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderUnreachableError,
    ServerNotFoundError,
    UnknownOptionKindError,
)
from iac_node_forge.application.ports import ProviderPort
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.infrastructure.proxmox import ProxmoxProvider
from tests.contract.conftest import TOKEN, WRONG_TOKEN, configuration_for
from tests.contract.mock_proxmox import MockProxmoxServer

pytestmark = pytest.mark.usefixtures("mock_proxmox")


def test_proxmox_provider_satisfies_provider_port(
    proxmox_provider: ProxmoxProvider,
) -> None:
    port: ProviderPort = proxmox_provider
    assert isinstance(port, ProviderPort)


@pytest.mark.parametrize("status", [401, 403])
def test_validate_access_fails_with_reason_for_rejected_status(
    mock_proxmox: MockProxmoxServer,
    proxmox_provider: ProxmoxProvider,
    status: int,
) -> None:
    mock_proxmox.state.forced_status = status

    result = proxmox_provider.validate_access(
        mock_proxmox.address, Credentials.single(TOKEN)
    )

    assert not result.succeeded
    assert str(status) in result.reason


def test_validate_access_fails_for_server_error(
    mock_proxmox: MockProxmoxServer, proxmox_provider: ProxmoxProvider
) -> None:
    mock_proxmox.state.forced_status = 500

    result = proxmox_provider.validate_access(
        mock_proxmox.address, Credentials.single(TOKEN)
    )

    assert not result.succeeded
    assert "500" in result.reason


def test_validate_access_fails_for_unreachable_address(
    proxmox_provider: ProxmoxProvider,
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    result = proxmox_provider.validate_access(
        f"http://127.0.0.1:{port}", Credentials.single(TOKEN)
    )

    assert not result.succeeded
    # Windows retries a refused connection until the timeout, so either text.
    assert "could not be reached" in result.reason or "did not answer" in result.reason


def test_validate_access_fails_when_provider_is_slower_than_timeout(
    mock_proxmox: MockProxmoxServer,
) -> None:
    mock_proxmox.state.delay_seconds = 1.0
    provider = ProxmoxProvider(Credentials.single(TOKEN), timeout=0.2)

    result = provider.validate_access(mock_proxmox.address, Credentials.single(TOKEN))

    assert not result.succeeded
    assert "did not answer" in result.reason


def test_validate_access_fails_for_tls_error_against_plain_http_port(
    mock_proxmox: MockProxmoxServer, proxmox_provider: ProxmoxProvider
) -> None:
    https_address = mock_proxmox.address.replace("http://", "https://")

    result = proxmox_provider.validate_access(https_address, Credentials.single(TOKEN))

    assert not result.succeeded
    assert result.reason


def test_list_servers_raises_authentication_error_for_expired_token(
    mock_proxmox: MockProxmoxServer,
) -> None:
    provider = ProxmoxProvider(Credentials.single(WRONG_TOKEN), timeout=2.0)

    with pytest.raises(ProviderAuthenticationError):
        provider.list_servers(configuration_for(mock_proxmox.address))


def test_list_servers_raises_provider_error_for_server_error(
    mock_proxmox: MockProxmoxServer, proxmox_provider: ProxmoxProvider
) -> None:
    mock_proxmox.state.forced_status = 500

    with pytest.raises(ProviderError):
        proxmox_provider.list_servers(configuration_for(mock_proxmox.address))


def test_list_servers_raises_unreachable_error_on_timeout(
    mock_proxmox: MockProxmoxServer,
) -> None:
    mock_proxmox.state.delay_seconds = 1.0
    provider = ProxmoxProvider(Credentials.single(TOKEN), timeout=0.2)

    with pytest.raises(ProviderUnreachableError):
        provider.list_servers(configuration_for(mock_proxmox.address))


def test_list_server_options_raises_for_unknown_kind(
    mock_proxmox: MockProxmoxServer, proxmox_provider: ProxmoxProvider
) -> None:
    with pytest.raises(UnknownOptionKindError):
        proxmox_provider.list_server_options(
            configuration_for(mock_proxmox.address), "image"
        )


def test_only_get_requests_are_issued(
    mock_proxmox: MockProxmoxServer, proxmox_provider: ProxmoxProvider
) -> None:
    configuration = configuration_for(mock_proxmox.address)

    proxmox_provider.validate_access(mock_proxmox.address, Credentials.single(TOKEN))
    proxmox_provider.discover_capabilities(configuration)
    proxmox_provider.list_server_options(configuration, "node")
    proxmox_provider.list_server_options(configuration, "storage")
    proxmox_provider.list_servers(configuration)
    proxmox_provider.get_server(configuration, "100")

    assert mock_proxmox.state.requests
    assert mock_proxmox.methods_used == {"GET"}


def test_requests_hit_the_documented_paths(
    mock_proxmox: MockProxmoxServer, proxmox_provider: ProxmoxProvider
) -> None:
    configuration = configuration_for(mock_proxmox.address)

    proxmox_provider.list_server_options(configuration, "storage")
    proxmox_provider.list_servers(configuration)

    paths = [request.path for request in mock_proxmox.state.requests]
    assert paths == [
        "/api2/json/nodes",
        "/api2/json/nodes/pve1/storage",
        "/api2/json/nodes/pve2/storage",
        "/api2/json/cluster/resources",
    ]


def test_secret_never_appears_in_errors_reprs_or_logs(
    mock_proxmox: MockProxmoxServer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    configuration = configuration_for(mock_proxmox.address)
    texts: list[str] = []
    wrong = ProxmoxProvider(Credentials.single(TOKEN + "x"), timeout=0.2)
    right = ProxmoxProvider(Credentials.single(TOKEN), timeout=0.2)
    texts.extend([repr(wrong), repr(right), repr(Credentials.single(TOKEN))])

    with pytest.raises(ProviderAuthenticationError) as auth_error:
        wrong.list_servers(configuration)
    texts.append(str(auth_error.value))
    texts.append(
        wrong.validate_access(
            mock_proxmox.address, Credentials.single(TOKEN + "x")
        ).reason
    )

    mock_proxmox.state.forced_status = 500
    with pytest.raises(ProviderError) as server_error:
        right.list_servers(configuration)
    texts.append(str(server_error.value))
    mock_proxmox.state.forced_status = None

    mock_proxmox.state.delay_seconds = 1.0
    with pytest.raises(ProviderUnreachableError) as timeout_error:
        right.list_servers(configuration)
    texts.append(str(timeout_error.value))
    mock_proxmox.state.delay_seconds = 0.0

    with pytest.raises(ServerNotFoundError) as missing:
        right.get_server(configuration, "999")
    texts.append(str(missing.value))
    with pytest.raises(UnknownOptionKindError) as unknown:
        right.list_server_options(configuration, "image")
    texts.append(str(unknown.value))
    texts.extend(record.getMessage() for record in caplog.records)

    secret_part = TOKEN.split("=", 1)[1]
    assert texts
    assert not [t for t in texts if TOKEN in t or secret_part in t]
