"""ProviderPort and ServerPowerPort implementation for Contabo (ADR-0005).

The four credential values are exchanged for a bearer token with the OAuth2
password grant. The token is kept only in memory, reused, and replaced once
after a 401. Every API call carries a fresh ``x-request-id``. Every API call
is a GET except the four power POSTs of ``_POWER_ACTION_PATH``
(``/actions/start``, ``shutdown``, ``stop`` and ``restart`` of one instance);
the only other request is the token POST (ADR-0006).
"""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import Any

import httpx

from iac_node_forge.application import capabilities
from iac_node_forge.application.errors import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderPageLimitError,
    ProviderRateLimitError,
    ProviderServiceError,
    ServerNotFoundError,
    UnknownOptionKindError,
)
from iac_node_forge.domain.access_result import AccessResult
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.errors import MissingCredentialError
from iac_node_forge.domain.operation import Operation, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_option import ServerOption
from iac_node_forge.infrastructure import _http, _power

DEFAULT_AUTH_URL = (
    "https://auth.contabo.com/auth/realms/contabo/protocol/openid-connect/token"
)
DEFAULT_TIMEOUT_SECONDS = 10.0
OPTION_KIND_REGION = "region"
OPTION_KIND_IMAGE = "image"
PAGE_SIZE = 100
MAX_PAGES = 100
INSTANCES_PATH = "/v1/compute/instances"
DATA_CENTERS_PATH = "/v1/data-centers"
IMAGES_PATH = "/v1/compute/images"
DEFAULT_RESTART_SETTLE_SECONDS = 30.0
_UNKNOWN = "unknown"
_STATUS_RUNNING = "running"
_STATUS_STOPPED = "stopped"
_STATUS_ERROR = "error"
_ACCEPTED_WRITE_STATUSES = frozenset({200, 201, 202})
_RESTART_NOT_OBSERVED = (
    "the instance is running after the settle time; the restart itself could "
    "not be observed because Contabo reports no task"
)

# The only write requests this adapter can send, one path per power action.
_POWER_ACTION_PATH: Mapping[PowerAction, str] = MappingProxyType(
    {
        PowerAction.START: "start",
        PowerAction.SHUTDOWN: "shutdown",
        PowerAction.STOP: "stop",
        PowerAction.RESTART: "restart",
    }
)
# The status an instance has when the action is done; RESTART has none that
# differs from the starting status, so it is judged after the settle time.
_TARGET_STATUS: Mapping[PowerAction, str] = MappingProxyType(
    {
        PowerAction.START: _STATUS_RUNNING,
        PowerAction.SHUTDOWN: _STATUS_STOPPED,
        PowerAction.STOP: _STATUS_STOPPED,
    }
)
_STATUS_NOT_FOUND = 404
_STATUS_RATE_LIMITED = 429
_STATUS_SERVER_ERROR = 500

# JSON from the provider is untyped by nature; it is narrowed in this module.
type _Json = Any


@dataclass(slots=True)
class _TokenSession:
    """The credentials of one caller and the bearer token obtained for them."""

    credentials: Credentials
    token: str | None = field(default=None, repr=False)


class ContaboProvider:
    """Reads data centers, images and instances from the Contabo API.

    ``credentials`` must hold the four names of ``ProviderKind.CONTABO``. The
    API address of every call comes from the method arguments. ``auth_url`` is
    the token endpoint; a test may point it at a mock. ``verify`` is ``True``
    (system CAs), a CA bundle path, or an explicit ``False``.
    ``restart_settle_seconds`` is how long after a RESTART request the
    instance may be reported as restarted; ``clock`` gives the time stamped on
    operations. Both are options so a test controls them.
    """

    def __init__(
        self,
        credentials: Credentials,
        auth_url: str = DEFAULT_AUTH_URL,
        verify: bool | str = True,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        restart_settle_seconds: float = DEFAULT_RESTART_SETTLE_SECONDS,
        clock: _power.Clock = _power.utc_now,
    ) -> None:
        _require_all_names(credentials)
        self._session = _TokenSession(credentials)
        self._auth_url = auth_url
        self._verify = verify
        self._timeout = timeout
        self._restart_settle = timedelta(seconds=restart_settle_seconds)
        self._clock = clock

    def __repr__(self) -> str:
        return (
            f"ContaboProvider(auth_url={self._auth_url!r}, "
            f"verify={self._verify!r}, timeout={self._timeout!r})"
        )

    def validate_access(self, address: str, credentials: Credentials) -> AccessResult:
        """Request a token, then list one instance; never raises.

        A failed result names the step that failed. The token obtained here is
        not kept: it belongs to ``credentials``, not to this adapter.
        """
        session = _TokenSession(credentials)
        try:
            self._fetch_token(session)
        except (ProviderError, MissingCredentialError) as error:
            return AccessResult(False, f"token request failed: {error}")
        try:
            self._get_body(session, address, INSTANCES_PATH, {"size": 1})
        except ProviderError as error:
            return AccessResult(False, f"instance list request failed: {error}")
        return AccessResult(True)

    def discover_capabilities(
        self, configuration: ProviderConfiguration
    ) -> frozenset[Capability]:
        """Return the read capabilities and the four power capabilities."""
        return frozenset(
            Capability(name)
            for name in (
                capabilities.LIST_SERVERS,
                capabilities.LIST_SERVER_OPTIONS,
                capabilities.INSPECT_SERVER,
                *capabilities.POWER_ACTION_CAPABILITY.values(),
            )
        )

    def list_server_options(
        self, configuration: ProviderConfiguration, option_kind: str
    ) -> tuple[ServerOption, ...]:
        """Return ``region`` (data center) or ``image`` options.

        A region value is the data center slug, or its name when it has no
        slug. An image value is the ``imageId``, which is what a later create
        request needs. Raises UnknownOptionKindError for any other kind.
        """
        if option_kind == OPTION_KIND_REGION:
            items = self._get_pages(configuration.address, DATA_CENTERS_PATH)
            return _options(OPTION_KIND_REGION, items, ("slug", "name"))
        if option_kind == OPTION_KIND_IMAGE:
            items = self._get_pages(configuration.address, IMAGES_PATH)
            return _options(OPTION_KIND_IMAGE, items, ("imageId",))
        raise UnknownOptionKindError(
            f"Contabo offers no options of kind {option_kind!r}; valid kinds: "
            + ", ".join(ProviderKind.CONTABO.option_kinds)
        )

    def list_servers(self, configuration: ProviderConfiguration) -> tuple[Server, ...]:
        """Return every compute instance of the account."""
        items = self._get_pages(configuration.address, INSTANCES_PATH)
        return tuple(_to_server(item) for item in items if "instanceId" in item)

    def get_server(
        self, configuration: ProviderConfiguration, server_identifier: str
    ) -> Server:
        """Return one instance by its numeric id; raises ServerNotFoundError."""
        text = server_identifier.strip()
        if not (text.isascii() and text.isdecimal()):
            raise ServerNotFoundError(f"No server {server_identifier!r} at Contabo")
        response = self._authorised_get(
            self._session,
            configuration.address,
            f"{INSTANCES_PATH}/{int(text)}",
            {},
        )
        if response.status_code == _STATUS_NOT_FOUND:
            raise ServerNotFoundError(f"No server {server_identifier!r} at Contabo")
        items = _data_items(_body(response))
        if not items:
            raise ServerNotFoundError(f"No server {server_identifier!r} at Contabo")
        return _to_server(items[0])

    def request_power(
        self,
        configuration: ProviderConfiguration,
        server_identifier: str,
        action: PowerAction,
    ) -> Operation:
        """POST one action of an instance; Contabo gives no task reference.

        A provider failure (401 after renewal, 403, 429, 5xx, unreachable)
        gives a FAILED operation. Raises ServerNotFoundError when the
        identifier is not a number or the instance does not exist.
        """
        text = server_identifier.strip()
        if not (text.isascii() and text.isdecimal()):
            raise ServerNotFoundError(f"No server {server_identifier!r} at Contabo")
        started_at = self._clock()
        path = f"{INSTANCES_PATH}/{int(text)}/actions/{_POWER_ACTION_PATH[action]}"
        try:
            # Look the instance up first, so a write is never sent for an id
            # that does not exist (the same guarantee as the Proxmox adapter).
            self.get_server(configuration, server_identifier)
            response = self._authorised_post(self._session, configuration.address, path)
            if response.status_code == _STATUS_NOT_FOUND:
                raise ServerNotFoundError(f"No server {server_identifier!r} at Contabo")
            _body(response, _ACCEPTED_WRITE_STATUSES)
        except ServerNotFoundError:
            raise
        except ProviderError as error:
            return _power.failed_operation(
                configuration, server_identifier, action, started_at, str(error)
            )
        return _power.running_operation(
            configuration, server_identifier, action, started_at, None
        )

    def operation_state(
        self, configuration: ProviderConfiguration, operation: Operation
    ) -> Operation:
        """Read the instance status once and judge it against the action.

        START, SHUTDOWN and STOP succeed when the status equals the target;
        ``error`` fails; any other status is still RUNNING. RESTART succeeds
        only when asked at or after the settle time with status ``running``,
        and its detail says the transition was not observed.
        """
        if operation.has_ended:
            return operation
        status = self.get_server(configuration, operation.server_identifier).state
        if status == _STATUS_ERROR:
            return _power.end_operation(
                operation,
                OperationState.FAILED,
                self._clock,
                f"the instance reported status {status!r}",
            )
        if operation.action is PowerAction.RESTART:
            return self._judge_restart(operation, status)
        if status == _TARGET_STATUS[operation.action]:
            return _power.end_operation(
                operation, OperationState.SUCCEEDED, self._clock, None
            )
        return operation

    def _judge_restart(self, operation: Operation, status: str) -> Operation:
        settled = self._clock() >= operation.started_at + self._restart_settle
        if settled and status == _STATUS_RUNNING:
            return _power.end_operation(
                operation,
                OperationState.SUCCEEDED,
                self._clock,
                _RESTART_NOT_OBSERVED,
            )
        return operation

    def _get_pages(self, address: str, path: str) -> list[Mapping[str, _Json]]:
        items: list[Mapping[str, _Json]] = []
        for page in range(1, MAX_PAGES + 1):
            body = self._get_body(
                self._session, address, path, {"page": page, "size": PAGE_SIZE}
            )
            data = _data_items(body)
            items.extend(data)
            if not data or page >= _total_pages(body):
                return items
        raise ProviderPageLimitError(
            f"Contabo listing {path} has more than {MAX_PAGES} pages; not read"
        )

    def _get_body(
        self,
        session: _TokenSession,
        address: str,
        path: str,
        params: Mapping[str, int],
    ) -> Mapping[str, _Json]:
        return _body(self._authorised_get(session, address, path, params))

    def _authorised_get(
        self,
        session: _TokenSession,
        address: str,
        path: str,
        params: Mapping[str, int],
    ) -> httpx.Response:
        """GET with the session's token; renew it once after a 401."""
        url = _http.base_url(address) + path
        return self._authorised(
            session,
            lambda client, headers: client.get(url, params=params, headers=headers),
        )

    def _authorised_post(
        self, session: _TokenSession, address: str, path: str
    ) -> httpx.Response:
        """POST a power action with no body; renew the token once after a 401.

        The action is documented without a request body, so only the JSON
        content type is sent. A 401 means the request was not processed, so
        repeating it once with the new token cannot act twice.
        """
        url = _http.base_url(address) + path
        return self._authorised(
            session,
            lambda client, headers: client.post(
                url, headers={**headers, "Content-Type": "application/json"}
            ),
        )

    def _authorised(
        self,
        session: _TokenSession,
        call: Callable[[httpx.Client, dict[str, str]], httpx.Response],
    ) -> httpx.Response:
        if session.token is None:
            self._fetch_token(session)
        response = self._send_with_token(session, call)
        if response.status_code != 401:
            return response
        self._fetch_token(session)
        return self._send_with_token(session, call)

    def _send_with_token(
        self,
        session: _TokenSession,
        call: Callable[[httpx.Client, dict[str, str]], httpx.Response],
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {session.token}",
            "x-request-id": str(uuid.uuid4()),
        }
        return self._send(lambda client: call(client, headers))

    def _fetch_token(self, session: _TokenSession) -> None:
        credentials = session.credentials
        form = {
            "grant_type": "password",
            "client_id": credentials.get("client_id"),
            "client_secret": credentials.get("client_secret"),
            "username": credentials.get("api_user"),
            "password": credentials.get("api_password"),
        }
        session.token = None
        response = self._send(lambda client: client.post(self._auth_url, data=form))
        if response.status_code in (400, 401, 403):
            raise ProviderAuthenticationError(
                f"Contabo answered {response.status_code} at the token request: "
                "the four credential values were rejected"
            )
        token = _body(response).get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderError("the Contabo token answer has no access_token")
        session.token = token

    def _send(self, call: Callable[[httpx.Client], httpx.Response]) -> httpx.Response:
        try:
            with httpx.Client(
                verify=_http.tls_verify(self._verify), timeout=self._timeout
            ) as client:
                return call(client)
        except httpx.TransportError as error:
            raise _http.unreachable_error(error, self._timeout) from error


def _require_all_names(credentials: Credentials) -> None:
    for name in ProviderKind.CONTABO.credential_names:
        credentials.get(name)


def _body(
    response: httpx.Response, accepted: frozenset[int] = frozenset({200})
) -> Mapping[str, _Json]:
    status = response.status_code
    if status in (401, 403):
        raise ProviderAuthenticationError(
            f"Contabo answered {status}: token rejected or not permitted"
        )
    if status == _STATUS_RATE_LIMITED:
        raise ProviderRateLimitError(
            "Contabo answered 429: rate limit reached, try again later"
        )
    if status >= _STATUS_SERVER_ERROR:
        raise ProviderServiceError(f"Contabo answered {status}: service error")
    if status not in accepted:
        raise ProviderError(f"Contabo answered with status {status}")
    try:
        body = response.json()
    except ValueError as error:
        raise ProviderError("Contabo answered with invalid JSON") from error
    if not isinstance(body, Mapping):
        raise ProviderError("the Contabo answer has an unexpected shape")
    return body


def _data_items(body: Mapping[str, _Json]) -> list[Mapping[str, _Json]]:
    data = body.get("data")
    if not isinstance(data, list):
        raise ProviderError("the Contabo answer has no data array")
    return [item for item in data if isinstance(item, Mapping)]


def _total_pages(body: Mapping[str, _Json]) -> int:
    pagination = body.get("_pagination")
    if isinstance(pagination, Mapping):
        total = pagination.get("totalPages")
        if isinstance(total, int | float) and not isinstance(total, bool):
            return int(total)
    return 1


def _options(
    kind: str, items: list[Mapping[str, _Json]], keys: tuple[str, ...]
) -> tuple[ServerOption, ...]:
    options: list[ServerOption] = []
    for item in items:
        value = next((_text(item.get(key)) for key in keys if _text(item.get(key))), "")
        if value:
            options.append(ServerOption(kind, value))
    return tuple(options)


def _text(value: _Json) -> str:
    return value.strip() if isinstance(value, str) else ""


def _to_server(item: Mapping[str, _Json]) -> Server:
    identifier = str(item.get("instanceId"))
    return Server(
        server_identifier=identifier,
        name=_text(item.get("displayName")) or _text(item.get("name")) or identifier,
        state=_text(item.get("status")) or _UNKNOWN,
        size=_size_text(item.get("cpuCores"), item.get("ramMb")),
        addresses=_addresses(item),
        managed=False,
    )


def _size_text(cpu: _Json, memory_mib: _Json) -> str:
    parts: list[str] = []
    if _is_number(cpu):
        parts.append(f"{cpu:g} vCPU")
    if _is_number(memory_mib):
        parts.append(f"{int(memory_mib)} MiB")
    return ", ".join(parts) or _UNKNOWN


def _is_number(value: _Json) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _addresses(item: Mapping[str, _Json]) -> tuple[str, ...]:
    found: list[str] = []
    config = item.get("ipConfig")
    if isinstance(config, Mapping):
        found.extend(_ip(config.get(version)) for version in ("v4", "v6"))
    extra = item.get("additionalIps")
    if isinstance(extra, list):
        found.extend(
            _ip(entry.get("v4")) for entry in extra if isinstance(entry, Mapping)
        )
    return tuple(dict.fromkeys(address for address in found if address))


def _ip(block: _Json) -> str:
    return _text(block.get("ip")) if isinstance(block, Mapping) else ""
