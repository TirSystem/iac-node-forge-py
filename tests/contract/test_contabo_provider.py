"""Behaviours specific to the Contabo adapter, against the mock API."""

import inspect
import logging
import socket
import uuid

import pytest

from iac_node_forge.application.errors import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderPageLimitError,
    ProviderRateLimitError,
    ProviderServiceError,
    ProviderUnreachableError,
    ServerNotFoundError,
)
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.errors import MissingCredentialError
from iac_node_forge.infrastructure.contabo import ContaboProvider
from iac_node_forge.infrastructure.contabo import provider as contabo_module
from tests.contract.conftest import (
    CONTABO_VALUES,
    CONTABO_WRONG_VALUES,
    contabo_configuration_for,
)
from tests.contract.mock_contabo import TOKEN_PATH, MockContaboServer

pytestmark = pytest.mark.usefixtures("mock_contabo")


def _make(mock: MockContaboServer, timeout: float = 2.0) -> ContaboProvider:
    return ContaboProvider(
        Credentials(CONTABO_VALUES), auth_url=mock.auth_url, timeout=timeout
    )


def test_constructor_requires_all_four_credential_names(
    mock_contabo: MockContaboServer,
) -> None:
    incomplete = Credentials({"client_id": "a", "client_secret": "b"})

    with pytest.raises(MissingCredentialError):
        ContaboProvider(incomplete, auth_url=mock_contabo.auth_url)


def test_token_is_requested_once_and_reused(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    configuration = contabo_configuration_for(mock_contabo.address)

    contabo_provider.list_servers(configuration)
    contabo_provider.list_server_options(configuration, "region")
    contabo_provider.get_server(configuration, "100")

    assert len(mock_contabo.token_requests) == 1
    assert mock_contabo.state.tokens_issued == 1


def test_token_is_replaced_once_after_a_401(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    mock_contabo.state.token_expires_after = 1
    configuration = contabo_configuration_for(mock_contabo.address)

    first = contabo_provider.list_server_options(configuration, "region")
    second = contabo_provider.list_server_options(configuration, "image")

    assert first
    assert second
    assert mock_contabo.state.tokens_issued == 2
    paths = [r.path for r in mock_contabo.api_requests]
    assert paths == [
        "/v1/data-centers",
        "/v1/compute/images",  # refused: the token had expired
        "/v1/compute/images",  # repeated with the new token
    ]


def test_second_401_is_an_authentication_error(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    mock_contabo.state.token_expires_after = 0

    with pytest.raises(ProviderAuthenticationError):
        contabo_provider.list_servers(contabo_configuration_for(mock_contabo.address))

    assert len(mock_contabo.token_requests) == 2
    assert len(mock_contabo.api_requests) == 2


def test_wrong_credentials_raise_authentication_error_without_api_request(
    mock_contabo: MockContaboServer,
) -> None:
    provider = ContaboProvider(
        Credentials(CONTABO_WRONG_VALUES), auth_url=mock_contabo.auth_url, timeout=2.0
    )

    with pytest.raises(ProviderAuthenticationError):
        provider.list_servers(contabo_configuration_for(mock_contabo.address))

    assert mock_contabo.api_requests == []


def test_every_api_request_carries_a_fresh_uuid4_request_id(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    configuration = contabo_configuration_for(mock_contabo.address)

    contabo_provider.list_servers(configuration)
    contabo_provider.list_server_options(configuration, "image")
    contabo_provider.get_server(configuration, "101")

    ids = [request.request_id for request in mock_contabo.api_requests]
    assert len(ids) == 3
    assert all(i is not None and uuid.UUID(i).version == 4 for i in ids)
    assert len(set(ids)) == 3


def test_validate_access_names_the_token_step_for_wrong_credentials(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    result = contabo_provider.validate_access(
        mock_contabo.address, Credentials(CONTABO_WRONG_VALUES)
    )

    assert not result.succeeded
    assert "token request failed" in result.reason
    assert "401" in result.reason


def test_validate_access_names_the_instance_step_when_the_api_refuses_the_token(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    mock_contabo.state.token_expires_after = 0

    result = contabo_provider.validate_access(
        mock_contabo.address, Credentials(CONTABO_VALUES)
    )

    assert not result.succeeded
    assert "instance list request failed" in result.reason


def test_validate_access_asks_for_one_instance_only(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    contabo_provider.validate_access(mock_contabo.address, Credentials(CONTABO_VALUES))

    assert [(r.path, r.query) for r in mock_contabo.api_requests] == [
        ("/v1/compute/instances", "size=1")
    ]


def test_validate_access_fails_when_credentials_lack_a_name(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    result = contabo_provider.validate_access(
        mock_contabo.address, Credentials.single("only-one-value")
    )

    assert not result.succeeded
    assert "only-one-value" not in result.reason


def test_rate_limit_is_a_provider_error_naming_provider_and_status(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    configuration = contabo_configuration_for(mock_contabo.address)
    contabo_provider.list_servers(configuration)
    mock_contabo.state.forced_status = 429

    with pytest.raises(ProviderRateLimitError) as error:
        contabo_provider.list_servers(configuration)

    assert "Contabo" in str(error.value)
    assert "429" in str(error.value)


def test_server_error_is_a_provider_error_naming_provider_and_status(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    configuration = contabo_configuration_for(mock_contabo.address)
    contabo_provider.list_servers(configuration)
    mock_contabo.state.forced_status = 500

    with pytest.raises(ProviderServiceError) as error:
        contabo_provider.list_servers(configuration)

    assert "Contabo" in str(error.value)
    assert "500" in str(error.value)


@pytest.mark.parametrize("status", [429, 500])
def test_validate_access_reports_forced_status_in_reason(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider, status: int
) -> None:
    mock_contabo.state.forced_status = status

    result = contabo_provider.validate_access(
        mock_contabo.address, Credentials(CONTABO_VALUES)
    )

    assert not result.succeeded
    assert str(status) in result.reason


def test_validate_access_fails_for_unreachable_address(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    provider = ContaboProvider(
        Credentials(CONTABO_VALUES),
        auth_url=f"http://127.0.0.1:{port}{TOKEN_PATH}",
        timeout=1.0,
    )

    result = provider.validate_access(
        f"http://127.0.0.1:{port}", Credentials(CONTABO_VALUES)
    )

    assert not result.succeeded
    # Windows retries a refused connection until the timeout, so either text.
    assert "could not be reached" in result.reason or "did not answer" in result.reason


def test_list_servers_raises_unreachable_error_on_timeout(
    mock_contabo: MockContaboServer,
) -> None:
    mock_contabo.state.delay_seconds = 1.0
    provider = _make(mock_contabo, timeout=0.2)

    with pytest.raises(ProviderUnreachableError):
        provider.list_servers(contabo_configuration_for(mock_contabo.address))


def test_validate_access_fails_for_tls_error_against_plain_http_port(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    https_address = mock_contabo.address.replace("http://", "https://")
    provider = ContaboProvider(
        Credentials(CONTABO_VALUES),
        auth_url=mock_contabo.auth_url.replace("http://", "https://"),
        timeout=2.0,
    )

    result = provider.validate_access(https_address, Credentials(CONTABO_VALUES))

    assert not result.succeeded
    assert result.reason


def test_list_servers_follows_pagination_until_the_last_page(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    mock_contabo.state.instances = [
        {"instanceId": number, "name": f"vm{number}", "status": "running"}
        for number in range(1, 26)
    ]
    mock_contabo.state.max_page_size = 10

    servers = contabo_provider.list_servers(
        contabo_configuration_for(mock_contabo.address)
    )

    assert [s.server_identifier for s in servers] == [str(n) for n in range(1, 26)]
    queries = [r.query for r in mock_contabo.api_requests]
    assert [q.split("&")[0] for q in queries] == ["page=1", "page=2", "page=3"]


def test_listing_stops_with_a_specific_error_after_the_page_bound(
    mock_contabo: MockContaboServer,
    contabo_provider: ContaboProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contabo_module, "MAX_PAGES", 3)
    mock_contabo.state.reported_total_pages = 50

    with pytest.raises(ProviderPageLimitError):
        contabo_provider.list_servers(contabo_configuration_for(mock_contabo.address))

    assert len(mock_contabo.api_requests) == 3


def test_region_option_falls_back_to_the_name_without_a_slug(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    mock_contabo.state.data_centers = [{"name": "Singapore"}, {"slug": ""}]

    options = contabo_provider.list_server_options(
        contabo_configuration_for(mock_contabo.address), "region"
    )

    assert [o.value for o in options] == ["Singapore"]


def test_server_mapping_tolerates_missing_fields_and_repeated_addresses(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    mock_contabo.state.instances = [
        {
            "instanceId": 7,
            "name": "vmd7",
            "ipConfig": {"v4": {"ip": "203.0.113.7"}, "v6": {"ip": ""}},
            "additionalIps": [
                {"v4": {"ip": "203.0.113.7"}},
                {"v4": {"ip": "10.0.0.7"}},
            ],
        },
        {"name": "no id, skipped"},
    ]

    servers = contabo_provider.list_servers(
        contabo_configuration_for(mock_contabo.address)
    )

    assert len(servers) == 1
    server = servers[0]
    assert server.name == "vmd7"
    assert server.state == "unknown"
    assert server.size == "unknown"
    assert server.addresses == ("203.0.113.7", "10.0.0.7")
    assert not server.managed


def test_get_server_raises_not_found_for_empty_data(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    mock_contabo.state.empty_instance_data = True

    with pytest.raises(ServerNotFoundError):
        contabo_provider.get_server(
            contabo_configuration_for(mock_contabo.address), "100"
        )


@pytest.mark.parametrize("identifier", ["abc", "1e2", "../v1/secrets", "", "10 0"])
def test_get_server_raises_not_found_without_request_for_non_integer_identifier(
    mock_contabo: MockContaboServer,
    contabo_provider: ContaboProvider,
    identifier: str,
) -> None:
    with pytest.raises(ServerNotFoundError):
        contabo_provider.get_server(
            contabo_configuration_for(mock_contabo.address), identifier
        )

    assert mock_contabo.state.requests == []


def test_only_get_and_the_single_token_post_are_issued(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    configuration = contabo_configuration_for(mock_contabo.address)

    contabo_provider.validate_access(mock_contabo.address, Credentials(CONTABO_VALUES))
    contabo_provider.discover_capabilities(configuration)
    contabo_provider.list_server_options(configuration, "region")
    contabo_provider.list_server_options(configuration, "image")
    contabo_provider.list_servers(configuration)
    contabo_provider.get_server(configuration, "100")

    posts = [r for r in mock_contabo.state.requests if r.method == "POST"]
    assert mock_contabo.methods_used == {"GET", "POST"}
    assert {r.path for r in posts} == {TOKEN_PATH}
    assert {r.method for r in mock_contabo.api_requests} == {"GET"}


def test_requests_hit_the_documented_paths(
    mock_contabo: MockContaboServer, contabo_provider: ContaboProvider
) -> None:
    configuration = contabo_configuration_for(mock_contabo.address)

    contabo_provider.list_server_options(configuration, "region")
    contabo_provider.list_server_options(configuration, "image")
    contabo_provider.list_servers(configuration)
    contabo_provider.get_server(configuration, "100")

    assert [r.path for r in mock_contabo.api_requests] == [
        "/v1/data-centers",
        "/v1/compute/images",
        "/v1/compute/instances",
        "/v1/compute/instances/100",
    ]


def test_adapter_source_has_no_write_request() -> None:
    source = inspect.getsource(contabo_module)

    # The token request, and the power POST, the instance creation and the
    # cancellation, which all go through ``_authorised_post`` (ADR-0006,
    # ADR-0007, ADR-0008).
    assert source.count(".post(") == 3
    assert ".post(self._auth_url" in source
    # Its definition, the power action, the creation and the cancellation.
    assert source.count("_authorised_post(") == 4
    for write_call in (".put(", ".patch(", ".delete(", ".request("):
        assert write_call not in source
    for other in ("snapshot", "reinstall", "rescue", "resize", "upgrade"):
        assert other not in source


def test_secret_values_and_token_never_appear_in_errors_reprs_or_logs(
    mock_contabo: MockContaboServer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    configuration = contabo_configuration_for(mock_contabo.address)
    provider = _make(mock_contabo, timeout=0.2)
    wrong = ContaboProvider(
        Credentials(CONTABO_WRONG_VALUES), auth_url=mock_contabo.auth_url, timeout=0.2
    )
    texts = [repr(provider), repr(wrong), repr(Credentials(CONTABO_VALUES))]

    with pytest.raises(ProviderAuthenticationError) as auth_error:
        wrong.list_servers(configuration)
    texts.append(str(auth_error.value))
    provider.list_servers(configuration)
    token = mock_contabo.state.current_token
    assert token

    for status, expected in (
        (429, ProviderRateLimitError),
        (500, ProviderServiceError),
    ):
        mock_contabo.state.forced_status = status
        with pytest.raises(expected) as failure:
            provider.list_servers(configuration)
        texts.append(str(failure.value))
        texts.append(
            provider.validate_access(
                mock_contabo.address, Credentials(CONTABO_VALUES)
            ).reason
        )
    mock_contabo.state.forced_status = None

    mock_contabo.state.token_expires_after = 0
    with pytest.raises(ProviderAuthenticationError) as expired:
        provider.list_servers(configuration)
    texts.append(str(expired.value))
    mock_contabo.state.token_expires_after = None
    mock_contabo.state.delay_seconds = 1.0
    with pytest.raises(ProviderUnreachableError) as timeout_error:
        provider.list_servers(configuration)
    texts.append(str(timeout_error.value))
    mock_contabo.state.delay_seconds = 0.0
    with pytest.raises(ProviderError) as bad_kind:
        provider.list_server_options(configuration, "flavour")
    texts.append(str(bad_kind.value))
    texts.extend(record.getMessage() for record in caplog.records)

    secrets = [*CONTABO_VALUES.values(), CONTABO_WRONG_VALUES["api_password"], token]
    assert len(texts) > 8
    assert not [s for s in secrets for t in texts if s in t]
