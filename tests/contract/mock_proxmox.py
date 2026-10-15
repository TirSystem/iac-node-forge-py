"""In-process mock of the Proxmox VE API, for contract tests.

Answers the read endpoints the adapter uses in the documented ``{"data": ...}``
shape, checks the ``Authorization: PVEAPIToken=`` header, can be told to fail,
and records every request so a test can prove only GET was used.
"""

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

API_PREFIX = "/api2/json"
NOT_FOUND = 404
UNAUTHORIZED = 401
FORBIDDEN = 403
SERVER_ERROR = 500


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    """One request the mock received."""

    method: str
    path: str


@dataclass(slots=True)
class MockProxmoxState:
    """The data and failure mode the mock serves; tests may change it."""

    token: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    storages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    resources: list[dict[str, Any]] = field(default_factory=list)
    forced_status: int | None = None
    delay_seconds: float = 0.0
    requests: list[RecordedRequest] = field(default_factory=list)


class _Handler(BaseHTTPRequestHandler):
    server: "_MockHttpServer"

    def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
        state = self.server.state
        parts = urlsplit(self.path)
        state.requests.append(RecordedRequest("GET", parts.path))
        if state.delay_seconds:
            time.sleep(state.delay_seconds)
        if state.forced_status is not None:
            self._reply(state.forced_status, {"data": None})
        elif self.headers.get("Authorization") != f"PVEAPIToken={state.token}":
            self._reply(UNAUTHORIZED, {"data": None})
        else:
            self._route(parts.path, state)

    def do_POST(self) -> None:  # noqa: N802
        self._reject_write("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_write("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject_write("DELETE")

    def _reject_write(self, method: str) -> None:
        self.server.state.requests.append(
            RecordedRequest(method, urlsplit(self.path).path)
        )
        self._reply(405, {"data": None})

    def _route(self, path: str, state: MockProxmoxState) -> None:
        segments = path.removeprefix(API_PREFIX).strip("/").split("/")
        if segments == ["version"]:
            self._reply(200, {"data": {"version": "8.2.4", "release": "8.2"}})
        elif segments == ["nodes"]:
            self._reply(200, {"data": state.nodes})
        elif len(segments) == 3 and segments[0] == "nodes" and segments[2] == "storage":
            self._reply(200, {"data": state.storages.get(segments[1], [])})
        elif segments == ["cluster", "resources"]:
            self._reply(200, {"data": state.resources})
        else:
            self._reply(NOT_FOUND, {"data": None})

    def _reply(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep the test output quiet."""


class _MockHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: MockProxmoxState) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.state = state


class MockProxmoxServer:
    """Starts the mock on an ephemeral port of 127.0.0.1; a context manager."""

    def __init__(self, token: str) -> None:
        self.state = MockProxmoxState(token=token)
        self._server = _MockHttpServer(self.state)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def address(self) -> str:
        """The base address, such as ``http://127.0.0.1:41234``."""
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> "MockProxmoxServer":
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
