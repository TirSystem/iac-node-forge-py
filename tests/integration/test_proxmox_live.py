"""Read-only tests of ProxmoxProvider against a real Proxmox VE server.

They run only when ``PVE_TOKEN`` (the full ``USER@REALM!TOKENID=SECRET``
string) is in the environment, and are deselected by default. ``PVE_ADDRESS``
defaults to ``https://localhost:8006``; ``PVE_VERIFY`` is ``true`` (default),
``false`` or the path of a CA bundle.

The token is never printed, logged or put in an assertion message: checks that
involve it are reduced to a boolean before they are asserted.
"""

import os
from dataclasses import dataclass, field

import pytest

from iac_node_forge.application.errors import ProviderError, ServerNotFoundError
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.server import Server
from iac_node_forge.infrastructure.proxmox import ProxmoxProvider

pytestmark = pytest.mark.integration

DEFAULT_ADDRESS = "https://localhost:8006"
ABSENT_SERVER_IDENTIFIER = "999999999"
WRITE_WORDS = ("create", "update", "delete", "remove", "power", "snapshot")
SECRET_SEPARATOR = "="
WRONG_SECRET = "00000000-0000-0000-0000-000000000000"


@dataclass(frozen=True, slots=True, repr=False)
class LiveServer:
    """A real server to test against; it hides its token from any output."""

    provider: ProxmoxProvider
    configuration: ProviderConfiguration
    token: str = field(repr=False)

    def __repr__(self) -> str:
        return f"LiveServer(address={self.configuration.address!r})"

    @property
    def wrong_token(self) -> str:
        """A well-formed token whose secret part is wrong."""
        identity, _, _ = self.token.partition(SECRET_SEPARATOR)
        return f"{identity}{SECRET_SEPARATOR}{WRONG_SECRET}"

    @property
    def secret_texts(self) -> tuple[str, ...]:
        """Every string whose appearance in an output would be a leak."""
        _, _, secret = self.token.partition(SECRET_SEPARATOR)
        return tuple(text for text in (self.token, secret) if text)

    def leaks(self, text: str) -> bool:
        """Say whether ``text`` contains the token or its secret part."""
        return any(secret in text for secret in self.secret_texts)


def _verify_from_environment() -> bool | str:
    value = os.environ.get("PVE_VERIFY", "true").strip()
    lowered = value.lower()
    if lowered in ("", "true"):
        return True
    if lowered == "false":
        return False
    return value


@pytest.fixture(scope="module")
def live() -> LiveServer:
    token = os.environ.get("PVE_TOKEN", "").strip()
    if not token:
        pytest.skip("PVE_TOKEN is not set; no real Proxmox VE to test against")
    address = os.environ.get("PVE_ADDRESS", "").strip() or DEFAULT_ADDRESS
    configuration = ProviderConfiguration(
        ConfigurationId("live"), ProviderKind.PROXMOX_VE, "Live Proxmox VE", address
    )
    provider = ProxmoxProvider(
        Credentials.single(token), verify=_verify_from_environment(), timeout=15.0
    )
    return LiveServer(provider, configuration, token)


def test_validate_access_succeeds_with_token(live: LiveServer) -> None:
    result = live.provider.validate_access(
        live.configuration.address, Credentials.single(live.token)
    )

    assert result.succeeded, "access check failed; see the reason of the result"
    assert not result.reason


def test_validate_access_fails_for_wrong_token_without_raising(
    live: LiveServer,
) -> None:
    result = live.provider.validate_access(
        live.configuration.address, Credentials.single(live.wrong_token)
    )

    assert not result.succeeded
    assert result.reason
    leaked = live.leaks(result.reason) or WRONG_SECRET in result.reason
    assert not leaked


def test_discover_capabilities_declares_no_write_capability(
    live: LiveServer,
) -> None:
    capabilities = live.provider.discover_capabilities(live.configuration)
    write_names = [
        c.name for c in capabilities if any(word in c.name for word in WRITE_WORDS)
    ]

    assert capabilities
    assert not write_names


def test_list_server_options_returns_at_least_one_node(live: LiveServer) -> None:
    options = live.provider.list_server_options(live.configuration, "node")

    assert isinstance(options, tuple)
    assert len(options) >= 1
    assert all(option.kind == "node" for option in options)


def test_list_server_options_returns_tuple_for_storage(live: LiveServer) -> None:
    options = live.provider.list_server_options(live.configuration, "storage")

    assert isinstance(options, tuple)
    assert all(option.kind == "storage" for option in options)


def test_list_servers_returns_unmanaged_servers(live: LiveServer) -> None:
    servers = live.provider.list_servers(live.configuration)

    assert isinstance(servers, tuple)
    assert all(isinstance(server, Server) for server in servers)
    assert all(server.managed is False for server in servers)


def test_get_server_raises_not_found_for_absent_identifier(live: LiveServer) -> None:
    with pytest.raises(ServerNotFoundError) as caught:
        live.provider.get_server(live.configuration, ABSENT_SERVER_IDENTIFIER)

    leaked = live.leaks(str(caught.value))
    assert not leaked


def test_error_text_does_not_contain_token_secret(live: LiveServer) -> None:
    wrong = ProxmoxProvider(
        Credentials.single(live.wrong_token),
        verify=_verify_from_environment(),
        timeout=15.0,
    )

    with pytest.raises(ProviderError) as caught:
        wrong.list_servers(live.configuration)

    text = str(caught.value)
    leaked = live.leaks(text) or WRONG_SECRET in text
    assert not leaked
