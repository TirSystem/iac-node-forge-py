"""Read-only tests of ContaboProvider against a real Contabo account.

They run only when all four of ``CONTABO_CLIENT_ID``, ``CONTABO_CLIENT_SECRET``,
``CONTABO_API_USER`` and ``CONTABO_API_PASSWORD`` are in the environment, and
are deselected by default. ``CONTABO_API_URL`` is optional and defaults to
``https://api.contabo.com``.

No test creates, changes, starts, stops, reinstalls or cancels anything. No
credential value is printed, logged or put in an assertion message: checks that
involve a value are reduced to a boolean before they are asserted.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from iac_node_forge.application.errors import ServerNotFoundError
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.server import Server
from iac_node_forge.infrastructure.contabo import ContaboProvider

pytestmark = pytest.mark.integration

DEFAULT_API_URL = "https://api.contabo.com"
ABSENT_SERVER_IDENTIFIER = "2147483000"
WRITE_WORDS = ("provision", "start", "stop", "restart", "delete", "create", "update")
WRONG_PASSWORD = "not-the-api-password-0000"
ENVIRONMENT_NAMES = {
    "client_id": "CONTABO_CLIENT_ID",
    "client_secret": "CONTABO_CLIENT_SECRET",
    "api_user": "CONTABO_API_USER",
    "api_password": "CONTABO_API_PASSWORD",
}
TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True, slots=True, repr=False)
class LiveAccount:
    """A real account to test against; it hides its values from any output."""

    provider: ContaboProvider
    configuration: ProviderConfiguration
    values: dict[str, str] = field(repr=False)

    def __repr__(self) -> str:
        return f"LiveAccount(address={self.configuration.address!r})"

    @property
    def credentials(self) -> Credentials:
        return Credentials(dict(self.values))

    @property
    def wrong_password_credentials(self) -> Credentials:
        return Credentials({**self.values, "api_password": WRONG_PASSWORD})

    def leaks(self, text: str) -> bool:
        """Say whether ``text`` contains any credential value."""
        return any(value in text for value in (*self.values.values(), WRONG_PASSWORD))


@pytest.fixture(scope="module")
def live() -> LiveAccount:
    values = {
        name: os.environ.get(variable, "").strip()
        for name, variable in ENVIRONMENT_NAMES.items()
    }
    missing = [ENVIRONMENT_NAMES[name] for name, value in values.items() if not value]
    if missing:
        pytest.skip(
            "no real Contabo account to test against; not set: " + ", ".join(missing)
        )
    address = os.environ.get("CONTABO_API_URL", "").strip() or DEFAULT_API_URL
    configuration = ProviderConfiguration(
        ConfigurationId("live"), ProviderKind.CONTABO, "Live Contabo", address
    )
    provider = ContaboProvider(Credentials(dict(values)), timeout=TIMEOUT_SECONDS)
    # Guard against a silently substituted double: the real adapter is required.
    assert type(provider) is ContaboProvider
    return LiveAccount(provider, configuration, values)


def test_contabo_validate_access_succeeds_with_credentials(live: LiveAccount) -> None:
    result = live.provider.validate_access(live.configuration.address, live.credentials)

    assert result.succeeded, "access check failed; see the reason of the result"
    assert not result.reason


def test_contabo_validate_access_fails_for_wrong_password_without_raising(
    live: LiveAccount,
) -> None:
    result = live.provider.validate_access(
        live.configuration.address, live.wrong_password_credentials
    )

    assert not result.succeeded
    assert result.reason
    leaked = live.leaks(result.reason)
    assert not leaked


def test_contabo_capabilities_declare_no_write_capability(live: LiveAccount) -> None:
    capabilities = live.provider.discover_capabilities(live.configuration)
    write_names = [
        c.name for c in capabilities if any(word in c.name for word in WRITE_WORDS)
    ]

    assert capabilities
    assert not write_names


def test_contabo_list_server_options_returns_tuple_for_region(
    live: LiveAccount,
) -> None:
    options = live.provider.list_server_options(live.configuration, "region")

    assert isinstance(options, tuple)
    assert all(option.kind == "region" for option in options)


def test_contabo_list_server_options_returns_tuple_for_image(live: LiveAccount) -> None:
    options = live.provider.list_server_options(live.configuration, "image")

    assert isinstance(options, tuple)
    assert all(option.kind == "image" for option in options)


def test_contabo_list_servers_returns_unmanaged_servers(live: LiveAccount) -> None:
    servers = live.provider.list_servers(live.configuration)

    assert isinstance(servers, tuple)
    assert all(isinstance(server, Server) for server in servers)
    assert all(server.managed is False for server in servers)


def test_contabo_get_server_raises_not_found_for_absent_identifier(
    live: LiveAccount,
) -> None:
    with pytest.raises(ServerNotFoundError) as caught:
        live.provider.get_server(live.configuration, ABSENT_SERVER_IDENTIFIER)

    leaked = live.leaks(str(caught.value))
    assert not leaked


def test_contabo_adapter_repr_contains_no_credential_value(live: LiveAccount) -> None:
    leaked = live.leaks(repr(live.provider)) or live.leaks(str(live.provider))

    assert not leaked


@pytest.fixture
def requests_sent(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Record the method of every request any httpx client sends."""
    methods: list[str] = []
    original = httpx.Client.send

    # httpx keyword arguments are forwarded unchanged, so they stay untyped here.
    def recording_send(
        self: httpx.Client, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        methods.append(request.method)
        return original(self, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", recording_send)
    yield methods


def test_contabo_live_run_sends_only_get_and_the_token_post(
    live: LiveAccount, requests_sent: list[str]
) -> None:
    live.provider.validate_access(live.configuration.address, live.credentials)
    live.provider.discover_capabilities(live.configuration)
    live.provider.list_server_options(live.configuration, "region")
    live.provider.list_server_options(live.configuration, "image")
    live.provider.list_servers(live.configuration)
    with pytest.raises(ServerNotFoundError):
        live.provider.get_server(live.configuration, ABSENT_SERVER_IDENTIFIER)

    others = [method for method in requests_sent if method not in ("GET", "POST")]
    assert requests_sent
    assert not others
    # Every POST is a token request: the adapter has no other POST in its code.
    assert requests_sent.count("POST") >= 1
