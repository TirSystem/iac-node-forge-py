"""In-process mock of the Contabo authentication service and API.

One port serves the token endpoint (a form POST that issues a bearer token) and
the read endpoints with ``page``/``size``/``_pagination``. It requires the
issued bearer token and a UUID4 ``x-request-id``, can be told to fail, to
expire the token, to be slow, and records every request so a test can prove
that only GET, the token POST, the four power actions and the instance
creation (``POST /v1/compute/instances``) were used. Every other write
answers 405 and is recorded.
"""

import json
import math
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any
from urllib.parse import parse_qs, urlsplit

TOKEN_PATH = "/auth/realms/contabo/protocol/openid-connect/token"
INSTANCES_PATH = "/v1/compute/instances"
DATA_CENTERS_PATH = "/v1/data-centers"
IMAGES_PATH = "/v1/compute/images"
# The product listing path is UNVERIFIED (see the adapter); only this mock
# answers it.
PRODUCTS_PATH = "/v1/compute/products"
BAD_REQUEST = 400
UNAUTHORIZED = 401
NOT_FOUND = 404
METHOD_NOT_ALLOWED = 405
UNSUPPORTED_MEDIA_TYPE = 415
SERVER_ERROR = 500
PERIODS = frozenset({1, 12, 24})
CREATE_FIELDS = ("imageId", "productId", "region", "period", "displayName")
DEFAULT_PAGE_SIZE = 10
ACTIONS = frozenset({"start", "shutdown", "stop", "restart"})
_ACTION_PATH = re.compile(
    INSTANCES_PATH + r"/(?P<id>\d+)/actions/(?P<action>start|shutdown|stop|restart)"
)
_TARGET_AFTER = {
    "start": "running",
    "shutdown": "stopped",
    "stop": "stopped",
    "restart": "running",
}


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    """One request the mock received."""

    method: str
    path: str
    query: str = ""
    request_id: str | None = None


@dataclass(slots=True)
class MockTransition:
    """A status change the mock applies over the next reads of an instance."""

    target: str
    reads_left: int
    interim_status: str | None


@dataclass(slots=True)
class MockContaboState:
    """The accounts, data and failure modes the mock serves; tests may change it."""

    client_id: str
    client_secret: str
    api_user: str
    api_password: str
    data_centers: list[dict[str, Any]] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    products: list[dict[str, Any]] = field(default_factory=list)
    instances: list[dict[str, Any]] = field(default_factory=list)
    forced_status: int | None = None
    delay_seconds: float = 0.0
    # The issued token stops working after this many authorised API requests.
    token_expires_after: int | None = None
    # The largest page the mock gives, whatever ``size`` asks for.
    max_page_size: int | None = None
    # When set, ``_pagination.totalPages`` reports this and the last page repeats.
    reported_total_pages: int | None = None
    # An instance lookup answers 200 with an empty ``data`` array.
    empty_instance_data: bool = False
    # Answers every allow-listed power action POST with this status.
    power_forced_status: int | None = None
    # Answers every instance creation POST with this status.
    create_forced_status: int | None = None
    # The creation is accepted (201) but the answer names no instance.
    create_answers_without_instance: bool = False
    # The JSON body of every creation the mock accepted, in order.
    created: list[dict[str, Any]] = field(default_factory=list)
    # Reads of an instance that still show the old (or interim) status after
    # a power action, before the target status appears.
    transition_reads: int = 2
    # The status shown during those reads; None keeps the status as it was.
    interim_status: str | None = None
    # The transition ends in ``error`` instead of its target status.
    transition_fails: bool = False
    transitions: dict[str, "MockTransition"] = field(default_factory=dict)
    current_token: str | None = None
    tokens_issued: int = 0
    token_uses: int = 0
    requests: list[RecordedRequest] = field(default_factory=list)


class _Handler(BaseHTTPRequestHandler):
    server: "_MockHttpServer"

    def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
        state = self.server.state
        parts = urlsplit(self.path)
        request_id = self.headers.get("x-request-id")
        state.requests.append(
            RecordedRequest("GET", parts.path, parts.query, request_id)
        )
        if state.delay_seconds:
            time.sleep(state.delay_seconds)
        if parts.path == TOKEN_PATH:
            self._reply(METHOD_NOT_ALLOWED, {})
        elif state.forced_status is not None:
            self._reply(state.forced_status, {})
        elif not self._bearer_accepted(state):
            self._reply(UNAUTHORIZED, {"message": "invalid token"})
        elif not _is_uuid4(request_id):
            self._reply(BAD_REQUEST, {"message": "x-request-id must be a UUID4"})
        else:
            self._route(parts.path, parse_qs(parts.query), state)

    def do_POST(self) -> None:  # noqa: N802
        state = self.server.state
        parts = urlsplit(self.path)
        request_id = self.headers.get("x-request-id")
        state.requests.append(
            RecordedRequest("POST", parts.path, parts.query, request_id)
        )
        action = _ACTION_PATH.fullmatch(parts.path)
        if action is not None:
            self._power_action(action, request_id, state)
            return
        if parts.path == INSTANCES_PATH:
            self._create_instance(request_id, state)
            return
        if parts.path != TOKEN_PATH:
            self._reply(METHOD_NOT_ALLOWED, {})
            return
        if state.delay_seconds:
            time.sleep(state.delay_seconds)
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode())
        if state.forced_status is not None:
            self._reply(state.forced_status, {})
        elif self._grant_accepted(form, state):
            state.current_token = secrets.token_urlsafe(24)
            state.tokens_issued += 1
            state.token_uses = 0
            self._reply(
                200,
                {
                    "access_token": state.current_token,
                    "expires_in": 300,
                    "token_type": "Bearer",
                },
            )
        else:
            self._reply(UNAUTHORIZED, {"error": "invalid_grant"})

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_write("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject_write("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject_write("DELETE")

    def _reject_write(self, method: str) -> None:
        parts = urlsplit(self.path)
        self.server.state.requests.append(RecordedRequest(method, parts.path))
        self._reply(METHOD_NOT_ALLOWED, {})

    def _power_action(
        self, match: re.Match[str], request_id: str | None, state: MockContaboState
    ) -> None:
        if state.delay_seconds:
            time.sleep(state.delay_seconds)
        content_type = self.headers.get("Content-Type", "")
        if state.power_forced_status is not None:
            self._reply(state.power_forced_status, {})
        elif state.forced_status is not None:
            self._reply(state.forced_status, {})
        elif not self._bearer_accepted(state):
            self._reply(UNAUTHORIZED, {"message": "invalid token"})
        elif not _is_uuid4(request_id):
            self._reply(BAD_REQUEST, {"message": "x-request-id must be a UUID4"})
        elif content_type != "application/json":
            self._reply(UNSUPPORTED_MEDIA_TYPE, {"message": "json expected"})
        else:
            self._apply_action(match["id"], match["action"], state)

    def _create_instance(self, request_id: str | None, state: MockContaboState) -> None:
        if state.delay_seconds:
            time.sleep(state.delay_seconds)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if state.create_forced_status is not None:
            self._reply(state.create_forced_status, {})
        elif state.forced_status is not None:
            self._reply(state.forced_status, {})
        elif not self._bearer_accepted(state):
            self._reply(UNAUTHORIZED, {"message": "invalid token"})
        elif not _is_uuid4(request_id):
            self._reply(BAD_REQUEST, {"message": "x-request-id must be a UUID4"})
        elif self.headers.get("Content-Type", "") != "application/json":
            self._reply(UNSUPPORTED_MEDIA_TYPE, {"message": "json expected"})
        else:
            self._store_instance(raw, state)

    def _store_instance(self, raw: bytes, state: MockContaboState) -> None:
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        if not _creation_is_valid(body):
            self._reply(BAD_REQUEST, {"message": "invalid creation request"})
            return
        identifier = max([100, *(i["instanceId"] for i in state.instances)]) + 1
        state.created.append(body)
        if state.create_answers_without_instance:
            self._reply(201, {"data": [], "_links": {"self": self.path}})
            return
        state.instances.append(
            {
                "instanceId": identifier,
                "name": f"vmd{identifier}",
                "displayName": body["displayName"],
                "status": "provisioning",
                "cpuCores": 4,
                "ramMb": 8192,
                "region": body["region"],
                "productId": body["productId"],
                "imageId": body["imageId"],
                "ipConfig": {"v4": {"ip": f"203.0.113.{identifier % 250}"}},
                "additionalIps": [],
            }
        )
        state.transitions[str(identifier)] = MockTransition(
            "error" if state.transition_fails else "running",
            state.transition_reads,
            None,
        )
        self._reply(
            201,
            {
                "data": [{"instanceId": identifier, "status": "provisioning"}],
                "_links": {"self": self.path},
            },
        )

    def _apply_action(
        self, identifier: str, action: str, state: MockContaboState
    ) -> None:
        found = [i for i in state.instances if str(i.get("instanceId")) == identifier]
        if not found:
            self._reply(NOT_FOUND, {"message": "instance not found"})
            return
        instance = found[0]
        target = _TARGET_AFTER[action]
        state.transitions[identifier] = MockTransition(
            "error" if state.transition_fails else target,
            state.transition_reads,
            state.interim_status,
        )
        self._reply(
            201,
            {
                "data": [{"instanceId": instance["instanceId"], "action": action}],
                "_links": {"self": self.path},
            },
        )

    @staticmethod
    def _grant_accepted(form: dict[str, list[str]], state: MockContaboState) -> bool:
        def value(name: str) -> str | None:
            return form[name][0] if name in form else None

        return (
            value("grant_type") == "password"
            and value("client_id") == state.client_id
            and value("client_secret") == state.client_secret
            and value("username") == state.api_user
            and value("password") == state.api_password
        )

    def _bearer_accepted(self, state: MockContaboState) -> bool:
        header = self.headers.get("Authorization", "")
        if state.current_token is None or header != f"Bearer {state.current_token}":
            return False
        expires = state.token_expires_after
        if expires is not None and state.token_uses >= expires:
            return False
        state.token_uses += 1
        return True

    def _route(
        self, path: str, query: dict[str, list[str]], state: MockContaboState
    ) -> None:
        if path == INSTANCES_PATH:
            self._reply_page(state.instances, query, state)
        elif path == DATA_CENTERS_PATH:
            self._reply_page(state.data_centers, query, state)
        elif path == IMAGES_PATH:
            self._reply_page(state.images, query, state)
        elif path == PRODUCTS_PATH:
            self._reply_page(state.products, query, state)
        elif path.startswith(INSTANCES_PATH + "/"):
            self._reply_instance(path.removeprefix(INSTANCES_PATH + "/"), state)
        else:
            self._reply(NOT_FOUND, {"message": "not found"})

    def _reply_instance(self, identifier: str, state: MockContaboState) -> None:
        found = [
            item
            for item in state.instances
            if str(item.get("instanceId")) == identifier
            and not state.empty_instance_data
        ]
        for item in found:
            _advance_transition(item, state)
        if state.empty_instance_data:
            self._reply(200, {"data": [], "_links": {"self": self.path}})
            return
        if found:
            self._reply(200, {"data": found, "_links": {"self": self.path}})
        else:
            self._reply(NOT_FOUND, {"message": "instance not found"})

    def _reply_page(
        self,
        items: list[dict[str, Any]],
        query: dict[str, list[str]],
        state: MockContaboState,
    ) -> None:
        page = max(1, int(query.get("page", ["1"])[0]))
        size = max(1, int(query.get("size", [str(DEFAULT_PAGE_SIZE)])[0]))
        if state.max_page_size is not None:
            size = min(size, state.max_page_size)
        total_pages = max(1, math.ceil(len(items) / size))
        start = (page - 1) * size
        data = items[start : start + size]
        if state.reported_total_pages is not None:
            total_pages = state.reported_total_pages
            data = items[:size]
        self._reply(
            200,
            {
                "_pagination": {
                    "size": size,
                    "totalElements": len(items),
                    "totalPages": total_pages,
                    "page": page,
                },
                "data": data,
                "_links": {"self": self.path},
            },
        )

    def _reply(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep the test output quiet."""


def _creation_is_valid(body: object) -> bool:
    """Whether the body holds every field of a creation, with a valid period."""
    return (
        isinstance(body, dict)
        and all(field_name in body for field_name in CREATE_FIELDS)
        and body["period"] in PERIODS
        and isinstance(body["period"], int)
    )


def _advance_transition(instance: dict[str, Any], state: MockContaboState) -> None:
    """One read of an instance moves its pending transition one step."""
    identifier = str(instance.get("instanceId"))
    transition = state.transitions.get(identifier)
    if transition is None:
        return
    if transition.reads_left > 0:
        transition.reads_left -= 1
        if transition.interim_status is not None:
            instance["status"] = transition.interim_status
        return
    instance["status"] = transition.target
    del state.transitions[identifier]


def _is_uuid4(text: str | None) -> bool:
    if text is None:
        return False
    try:
        return uuid.UUID(text).version == 4 and str(uuid.UUID(text)) == text.lower()
    except ValueError:
        return False


class _MockHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: MockContaboState) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.state = state


class MockContaboServer:
    """Starts the mock on an ephemeral port of 127.0.0.1; a context manager."""

    def __init__(
        self, client_id: str, client_secret: str, api_user: str, api_password: str
    ) -> None:
        self.state = MockContaboState(client_id, client_secret, api_user, api_password)
        self._server = _MockHttpServer(self.state)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def address(self) -> str:
        """The API base address, such as ``http://127.0.0.1:41234``."""
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    @property
    def auth_url(self) -> str:
        """The token endpoint, on the same port as the API."""
        return self.address + TOKEN_PATH

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> "MockContaboServer":
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    @property
    def methods_used(self) -> set[str]:
        """The distinct HTTP methods received so far."""
        return {request.method for request in self.state.requests}

    @property
    def api_requests(self) -> list[RecordedRequest]:
        """The requests to API paths, without the token requests."""
        return [r for r in self.state.requests if r.path != TOKEN_PATH]

    @property
    def write_requests(self) -> list[RecordedRequest]:
        """Every non-GET request except the token request, 405 ones included."""
        return [
            r for r in self.state.requests if r.method != "GET" and r.path != TOKEN_PATH
        ]

    @property
    def token_requests(self) -> list[RecordedRequest]:
        """The requests to the token path."""
        return [r for r in self.state.requests if r.path == TOKEN_PATH]
