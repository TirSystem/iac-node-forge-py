"""In-process mock of the Proxmox VE API, for contract tests.

Answers the read endpoints the adapter uses in the documented ``{"data": ...}``
shape, checks the ``Authorization: PVEAPIToken=`` header, can be told to fail,
and records every request so a test can prove which writes were received.

The only writes it accepts are the four power POSTs
``/nodes/{node}/{qemu|lxc}/{vmid}/status/{start|shutdown|stop|reboot}``, the
two creations ``POST /nodes/{node}/{qemu|lxc}``, the deletion
``DELETE /nodes/{node}/{qemu|lxc}/{vmid}``, the dump ``POST /nodes/{node}/vzdump``
and the restore, which is a ``POST`` to the same path as a creation but with
``force``; each answers a task identifier (UPID) whose status stays ``running``
for a number of reads and then becomes ``stopped`` with an exit status (a
successful creation adds the guest to the resources then, a successful deletion
takes it out, a successful dump adds an archive to the content of its storage,
a successful restore replaces the guest by the state the archive holds). Like
Proxmox VE, a deletion of a running guest is refused with 500, and so is a
restore onto an existing guest without ``force``. The mock also refuses a
restore over a running guest: what Proxmox VE does there is not documented
(B12), and the product never relies on it. Every other write verb or path
answers 405 and is recorded. ``classify_write`` names the class of a recorded
write by its method, path and form, so a test can tell a creation from a
restore.
"""

import copy
import json
import re
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

API_PREFIX = "/api2/json"
NOT_FOUND = 404
UNAUTHORIZED = 401
FORBIDDEN = 403
SERVER_ERROR = 500
METHOD_NOT_ALLOWED = 405
POWER_VERBS = frozenset({"start", "shutdown", "stop", "reboot"})
_POWER_PATH = re.compile(
    r"/nodes/(?P<node>[^/]+)/(?P<type>qemu|lxc)/(?P<vmid>\d+)"
    r"/status/(?P<verb>start|shutdown|stop|reboot)"
)
_CREATE_PATH = re.compile(r"/nodes/(?P<node>[^/]+)/(?P<type>qemu|lxc)")
_VZDUMP_PATH = re.compile(r"/nodes/(?P<node>[^/]+)/vzdump")
# The body fields that make a POST to a creation path a restore (ADR-0010).
RESTORE_FIELDS = frozenset({"force", "restore", "archive"})
WRITE_CLASSES = frozenset({"power", "create", "restore", "vzdump", "delete"})
UNLISTED = "unlisted"
ARCHIVE_SIZE = 1048576
_DELETE_PATH = re.compile(r"/nodes/(?P<node>[^/]+)/(?P<type>qemu|lxc)/(?P<vmid>\d+)")
_FIRST_VMID = 100
_STATUS_AFTER = {
    "start": "running",
    "shutdown": "stopped",
    "stop": "stopped",
    "reboot": "running",
}


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    """One request the mock received, with the form fields of a POST."""

    method: str
    path: str
    form: dict[str, str] = field(default_factory=dict)
    query: str = ""


@dataclass(slots=True)
class MockTask:
    """A task the mock created; it ends after ``polls_left`` status reads."""

    node: str
    guest_id: str
    verb: str
    polls_left: int
    exitstatus: str
    # The guest a creation adds to the resources when it ends with OK.
    creation: dict[str, Any] | None = None
    # Whether the guest leaves the resources when a deletion ends with OK.
    destroys: bool = False
    # The archive a dump adds to the content of "<node>/<storage>" when it ends
    # with OK, and the state of the guest it holds.
    archive: tuple[str, dict[str, Any], dict[str, Any]] | None = None
    # The state a restore puts in the resources when it ends with OK.
    restored: dict[str, Any] | None = None


@dataclass(slots=True)
class MockProxmoxState:
    """The data and failure mode the mock serves; tests may change it."""

    token: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    storages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Volumes by "<node>/<storage>"; each item has "volid" and "content".
    storage_content: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    resources: list[dict[str, Any]] = field(default_factory=list)
    forced_status: int | None = None
    # Answers every allow-listed power POST with this status; reads still work.
    power_forced_status: int | None = None
    # Answers every creation POST with this status; reads still work.
    create_forced_status: int | None = None
    # A creation is carried out but its answer holds no task identifier, as when
    # the answer is lost: the guest exists at once and no task is created.
    create_answers_without_task: bool = False
    # Answers every deletion with this status; reads still work.
    delete_forced_status: int | None = None
    # The query options of every deletion the mock accepted, in order.
    deleted: list[dict[str, str]] = field(default_factory=list)
    # The form fields of every creation the mock accepted, in order.
    created: list[dict[str, str]] = field(default_factory=list)
    # Answers every dump POST with this status; reads still work.
    dump_forced_status: int | None = None
    # Answers every restore POST with this status; reads still work.
    restore_forced_status: int | None = None
    # The form fields of every dump and every restore the mock accepted.
    dumped: list[dict[str, str]] = field(default_factory=list)
    restored: list[dict[str, str]] = field(default_factory=list)
    # The guest state each archive holds, by volume identifier.
    archive_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    # A dump ends with OK but leaves no archive in the storage content.
    dump_leaves_no_archive: bool = False
    # The archive entries leave out their creation time.
    archive_without_ctime: bool = False
    # The creation time of the next archives, in seconds; None uses the clock.
    archive_ctime: int | None = None
    last_archive_ctime: int = 0
    # Status reads that answer ``running`` before a task becomes ``stopped``.
    task_polls_before_end: int = 2
    # The exit status a task ends with.
    task_exitstatus: str = "OK"
    delay_seconds: float = 0.0
    tasks: dict[str, MockTask] = field(default_factory=dict)
    requests: list[RecordedRequest] = field(default_factory=list)


class _Handler(BaseHTTPRequestHandler):
    server: "_MockHttpServer"

    def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
        state = self.server.state
        parts = urlsplit(self.path)
        state.requests.append(RecordedRequest("GET", parts.path, query=parts.query))
        if state.delay_seconds:
            time.sleep(state.delay_seconds)
        if state.forced_status is not None:
            self._reply(state.forced_status, {"data": None})
        elif self.headers.get("Authorization") != f"PVEAPIToken={state.token}":
            self._reply(UNAUTHORIZED, {"data": None})
        else:
            self._route(parts.path, state)

    def do_POST(self) -> None:  # noqa: N802
        state = self.server.state
        path = urlsplit(self.path).path
        form = self._read_form()
        state.requests.append(RecordedRequest("POST", path, form))
        relative = path.removeprefix(API_PREFIX)
        match = _POWER_PATH.fullmatch(relative)
        create = _CREATE_PATH.fullmatch(relative)
        dump = _VZDUMP_PATH.fullmatch(relative)
        if create is not None and RESTORE_FIELDS & form.keys():
            self._restore_guest(create, form, state)
        elif create is not None:
            self._create_guest(create, form, state)
        elif dump is not None:
            self._dump_guest(dump["node"], form, state)
        elif match is None:
            self._reply(METHOD_NOT_ALLOWED, {"data": None})
        elif self.headers.get("Authorization") != f"PVEAPIToken={state.token}":
            self._reply(UNAUTHORIZED, {"data": None})
        elif state.power_forced_status is not None:
            self._reply(state.power_forced_status, {"data": None})
        elif state.forced_status is not None:
            self._reply(state.forced_status, {"data": None})
        else:
            self._start_power_task(match, state)

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_write("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject_write("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        state = self.server.state
        parts = urlsplit(self.path)
        state.requests.append(RecordedRequest("DELETE", parts.path))
        match = _DELETE_PATH.fullmatch(parts.path.removeprefix(API_PREFIX))
        if match is None:
            self._reply(METHOD_NOT_ALLOWED, {"data": None})
        elif self.headers.get("Authorization") != f"PVEAPIToken={state.token}":
            self._reply(UNAUTHORIZED, {"data": None})
        elif state.delete_forced_status is not None:
            self._reply(state.delete_forced_status, {"data": None})
        elif state.forced_status is not None:
            self._reply(state.forced_status, {"data": None})
        else:
            self._start_delete_task(match, parse_qs(parts.query), state)

    def _reject_write(self, method: str) -> None:
        self.server.state.requests.append(
            RecordedRequest(method, urlsplit(self.path).path)
        )
        self._reply(METHOD_NOT_ALLOWED, {"data": None})

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        return {
            key: values[0]
            for key, values in parse_qs(self.rfile.read(length).decode()).items()
        }

    def _create_guest(
        self, match: re.Match[str], form: dict[str, str], state: MockProxmoxState
    ) -> None:
        node, guest_type = match["node"], match["type"]
        if self.headers.get("Authorization") != f"PVEAPIToken={state.token}":
            self._reply(UNAUTHORIZED, {"data": None})
        elif state.create_forced_status is not None:
            self._reply(state.create_forced_status, {"data": None})
        elif state.forced_status is not None:
            self._reply(state.forced_status, {"data": None})
        elif not _creation_is_valid(form, node, guest_type, state):
            self._reply(SERVER_ERROR, {"data": None})
        else:
            state.created.append(form)
            if state.create_answers_without_task:
                state.resources.append(_new_guest(form, node, guest_type))
                self._reply(200, {"data": None})
                return
            upid = (
                f"UPID:{node}:{len(state.tasks) + 1:08X}:00000000:00000000:"
                f"{guest_type}create:{form['vmid']}:root@pam!forge:"
            )
            state.tasks[upid] = MockTask(
                node,
                form["vmid"],
                "create",
                state.task_polls_before_end,
                state.task_exitstatus,
                _new_guest(form, node, guest_type),
            )
            self._reply(200, {"data": upid})

    def _dump_guest(
        self, node: str, form: dict[str, str], state: MockProxmoxState
    ) -> None:
        guest = _find_guest(state, node, form.get("vmid", ""))
        storage = form.get("storage", "")
        holds_backups = any(
            item.get("storage") == storage and "backup" in item.get("content", "")
            for item in state.storages.get(node, [])
        )
        if self.headers.get("Authorization") != f"PVEAPIToken={state.token}":
            self._reply(UNAUTHORIZED, {"data": None})
        elif state.dump_forced_status is not None:
            self._reply(state.dump_forced_status, {"data": None})
        elif state.forced_status is not None:
            self._reply(state.forced_status, {"data": None})
        elif guest is None or not holds_backups or "notes-template" not in form:
            self._reply(SERVER_ERROR, {"data": None})
        else:
            state.dumped.append(form)
            self._reply(200, {"data": _start_dump(node, storage, guest, form, state)})

    def _restore_guest(
        self, match: re.Match[str], form: dict[str, str], state: MockProxmoxState
    ) -> None:
        node, guest_type = match["node"], match["type"]
        volid = form.get("ostemplate" if guest_type == "lxc" else "archive", "")
        existing = _find_guest(state, node, form.get("vmid", ""))
        if self.headers.get("Authorization") != f"PVEAPIToken={state.token}":
            self._reply(UNAUTHORIZED, {"data": None})
        elif state.restore_forced_status is not None:
            self._reply(state.restore_forced_status, {"data": None})
        elif state.forced_status is not None:
            self._reply(state.forced_status, {"data": None})
        elif not _restore_is_valid(form, guest_type, existing, volid, state):
            self._reply(SERVER_ERROR, {"data": None})
        else:
            state.restored.append(form)
            upid = (
                f"UPID:{node}:{len(state.tasks) + 1:08X}:00000000:00000000:"
                f"{guest_type}restore:{form['vmid']}:root@pam!forge:"
            )
            state.tasks[upid] = MockTask(
                node,
                form["vmid"],
                "restore",
                state.task_polls_before_end,
                state.task_exitstatus,
                restored={**state.archive_states[volid], "status": "stopped"},
            )
            self._reply(200, {"data": upid})

    def _start_delete_task(
        self,
        match: re.Match[str],
        query: dict[str, list[str]],
        state: MockProxmoxState,
    ) -> None:
        node, guest_type, vmid = match["node"], match["type"], match["vmid"]
        known = [
            item
            for item in state.resources
            if str(item.get("vmid")) == vmid
            and item.get("node") == node
            and item.get("type") == guest_type
        ]
        if not known or known[0].get("status") == "running":
            self._reply(SERVER_ERROR, {"data": None})
            return
        state.deleted.append({key: values[0] for key, values in query.items()})
        upid = (
            f"UPID:{node}:{len(state.tasks) + 1:08X}:00000000:00000000:"
            f"{guest_type}destroy:{vmid}:root@pam!forge:"
        )
        state.tasks[upid] = MockTask(
            node,
            vmid,
            "destroy",
            state.task_polls_before_end,
            state.task_exitstatus,
            destroys=True,
        )
        self._reply(200, {"data": upid})

    def _start_power_task(self, match: re.Match[str], state: MockProxmoxState) -> None:
        node, vmid, verb = match["node"], match["vmid"], match["verb"]
        known = [
            item
            for item in state.resources
            if str(item.get("vmid")) == vmid and item.get("node") == node
        ]
        if not known:
            self._reply(SERVER_ERROR, {"data": None})
            return
        upid = (
            f"UPID:{node}:{len(state.tasks) + 1:08X}:00000000:00000000:"
            f"qm{verb}:{vmid}:root@pam!forge:"
        )
        state.tasks[upid] = MockTask(
            node, vmid, verb, state.task_polls_before_end, state.task_exitstatus
        )
        self._reply(200, {"data": upid})

    def _reply_task_status(self, node: str, upid: str, state: MockProxmoxState) -> None:
        task = state.tasks.get(upid)
        if task is None or task.node != node:
            self._reply(NOT_FOUND, {"data": None})
            return
        if task.polls_left > 0:
            task.polls_left -= 1
            self._reply(
                200, {"data": {"status": "running", "upid": upid, "node": node}}
            )
            return
        if task.exitstatus == "OK" and task.archive is not None:
            _store_archive(task.archive, state)
        elif task.exitstatus == "OK" and task.restored is not None:
            _apply_restore(task.guest_id, task.restored, state)
        elif task.exitstatus == "OK" and task.creation is not None:
            state.resources.append(task.creation)
        elif task.exitstatus == "OK" and task.destroys:
            state.resources = [
                item
                for item in state.resources
                if str(item.get("vmid")) != task.guest_id
            ]
        elif task.exitstatus == "OK":
            for item in state.resources:
                if str(item.get("vmid")) == task.guest_id:
                    item["status"] = _STATUS_AFTER[task.verb]
        self._reply(
            200,
            {
                "data": {
                    "status": "stopped",
                    "exitstatus": task.exitstatus,
                    "upid": upid,
                    "node": node,
                }
            },
        )

    def _route(self, path: str, state: MockProxmoxState) -> None:
        segments = path.removeprefix(API_PREFIX).strip("/").split("/")
        if segments == ["version"]:
            self._reply(200, {"data": {"version": "8.2.4", "release": "8.2"}})
        elif segments == ["nodes"]:
            self._reply(200, {"data": state.nodes})
        elif len(segments) == 3 and segments[0] == "nodes" and segments[2] == "storage":
            self._reply_storages(
                segments[1], parse_qs(urlsplit(self.path).query), state
            )
        elif (
            len(segments) == 5
            and segments[0] == "nodes"
            and segments[2] == "storage"
            and segments[4] == "content"
        ):
            self._reply_content(
                f"{segments[1]}/{segments[3]}",
                parse_qs(urlsplit(self.path).query),
                state,
            )
        elif segments == ["cluster", "nextid"]:
            self._reply(200, {"data": str(_next_vmid(state))})
        elif segments == ["cluster", "resources"]:
            self._reply(200, {"data": state.resources})
        elif (
            len(segments) == 5
            and segments[0] == "nodes"
            and segments[2] == "tasks"
            and segments[4] == "status"
        ):
            self._reply_task_status(segments[1], unquote(segments[3]), state)
        else:
            self._reply(NOT_FOUND, {"data": None})

    def _reply_storages(
        self, node: str, query: dict[str, list[str]], state: MockProxmoxState
    ) -> None:
        wanted = query.get("content", [None])[0]
        storages = [
            item
            for item in state.storages.get(node, [])
            if wanted is None or wanted in str(item.get("content", "")).split(",")
        ]
        self._reply(200, {"data": storages})

    def _reply_content(
        self, key: str, query: dict[str, list[str]], state: MockProxmoxState
    ) -> None:
        wanted = query.get("content", [None])[0]
        vmid = query.get("vmid", [None])[0]
        volumes = [
            item
            for item in state.storage_content.get(key, [])
            if (wanted is None or item.get("content") == wanted)
            and (vmid is None or str(item.get("vmid")) == vmid)
        ]
        self._reply(200, {"data": volumes})

    def _reply(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep the test output quiet."""


def _next_vmid(state: MockProxmoxState) -> int:
    taken = [int(item["vmid"]) for item in state.resources if "vmid" in item]
    return max([_FIRST_VMID - 1, *taken]) + 1


def _creation_is_valid(
    form: dict[str, str], node: str, guest_type: str, state: MockProxmoxState
) -> bool:
    """Whether the form holds what a real creation needs, as Proxmox VE checks."""
    required = {"vmid", "memory", "cores", "net0"}
    if guest_type == "lxc":
        required |= {"hostname", "ostemplate", "rootfs"}
    else:
        required |= {"name", "scsi0", "ide2"}
    taken = any(str(item.get("vmid")) == form.get("vmid") for item in state.resources)
    known_node = any(item.get("node") == node for item in state.nodes)
    return required <= form.keys() and known_node and not taken


def _find_guest(state: MockProxmoxState, node: str, vmid: str) -> dict[str, Any] | None:
    for item in state.resources:
        if str(item.get("vmid")) == vmid and item.get("node") == node:
            return item
    return None


def _start_dump(
    node: str,
    storage: str,
    guest: dict[str, Any],
    form: dict[str, str],
    state: MockProxmoxState,
) -> str:
    """Create the dump task; the archive appears when the task ends with OK."""
    vmid, guest_type = str(guest["vmid"]), str(guest["type"])
    upid = (
        f"UPID:{node}:{len(state.tasks) + 1:08X}:00000000:00000000:"
        f"vzdump:{vmid}:root@pam!forge:"
    )
    notes = (
        form["notes-template"]
        .replace("{{guestname}}", str(guest.get("name", "")))
        .replace("{{vmid}}", vmid)
        .replace("{{node}}", node)
        .replace("{{cluster}}", "cluster")
    )
    created = _next_ctime(state)
    stamp = time.strftime("%Y_%m_%d-%H_%M_%S", time.gmtime(created))
    extension = "tar" if guest_type == "lxc" else "vma"
    item: dict[str, Any] = {
        "volid": f"{storage}:backup/vzdump-{guest_type}-{vmid}-{stamp}.{extension}",
        "content": "backup",
        "format": extension,
        "size": ARCHIVE_SIZE,
        "vmid": int(vmid),
        "notes": notes,
        "protected": 0,
    }
    if not state.archive_without_ctime:
        item["ctime"] = created
    state.tasks[upid] = MockTask(
        node,
        vmid,
        "dump",
        state.task_polls_before_end,
        state.task_exitstatus,
        archive=(f"{node}/{storage}", item, copy.deepcopy(guest)),
    )
    return upid


def _next_ctime(state: MockProxmoxState) -> int:
    """Return a creation time that is never reused, so archives differ."""
    wanted = (
        state.archive_ctime if state.archive_ctime is not None else int(time.time())
    )
    state.last_archive_ctime = max(wanted, state.last_archive_ctime + 1)
    return state.last_archive_ctime


def _store_archive(
    archive: tuple[str, dict[str, Any], dict[str, Any]], state: MockProxmoxState
) -> None:
    key, item, guest_state = archive
    if state.dump_leaves_no_archive:
        return
    state.storage_content.setdefault(key, []).append(item)
    state.archive_states[item["volid"]] = guest_state


def _apply_restore(
    vmid: str, restored: dict[str, Any], state: MockProxmoxState
) -> None:
    """Replace the guest by the state the archive holds, or add it when absent."""
    for index, item in enumerate(state.resources):
        if str(item.get("vmid")) == vmid:
            state.resources[index] = copy.deepcopy(restored)
            return
    state.resources.append(copy.deepcopy(restored))


def _restore_is_valid(
    form: dict[str, str],
    guest_type: str,
    existing: dict[str, Any] | None,
    volid: str,
    state: MockProxmoxState,
) -> bool:
    """Whether the form is a restore Proxmox VE accepts (ADR-0010, B10, B11)."""
    if guest_type == "lxc" and form.get("restore") != "1":
        return False
    if "vmid" not in form or volid not in state.archive_states:
        return False
    if existing is None:
        return True
    # Without force a restore onto an existing guest is refused (B11); a running
    # guest is refused here because B12 is not documented.
    return form.get("force") == "1" and existing.get("status") != "running"


def classify_write(request: RecordedRequest) -> str:
    """Name the class of a recorded write: one of ``WRITE_CLASSES`` or ``unlisted``.

    A POST to ``.../lxc`` or ``.../qemu`` is a creation only when its form has none
    of ``force``, ``restore`` and ``archive``, and a restore only when it has
    ``force=1`` and, for ``lxc``, ``restore=1`` with ``ostemplate`` (and no
    ``archive``), for ``qemu``, ``archive`` (and neither ``restore`` nor
    ``ostemplate``). Any other combination is ``unlisted`` (ADR-0010).
    """
    relative = request.path.removeprefix(API_PREFIX)
    if request.method == "DELETE":
        return "delete" if _DELETE_PATH.fullmatch(relative) else UNLISTED
    if request.method != "POST":
        return UNLISTED
    if _POWER_PATH.fullmatch(relative) or _VZDUMP_PATH.fullmatch(relative):
        return "power" if _POWER_PATH.fullmatch(relative) else "vzdump"
    create = _CREATE_PATH.fullmatch(relative)
    if create is None:
        return UNLISTED
    form = request.form
    if not RESTORE_FIELDS & form.keys():
        return "create"
    return "restore" if _is_restore_form(create["type"], form) else UNLISTED


def _is_restore_form(guest_type: str, form: dict[str, str]) -> bool:
    if form.get("force") != "1":
        return False
    if guest_type == "lxc":
        return (
            form.get("restore") == "1"
            and "ostemplate" in form
            and "archive" not in form
        )
    return "archive" in form and "restore" not in form and "ostemplate" not in form


def _new_guest(form: dict[str, str], node: str, guest_type: str) -> dict[str, Any]:
    return {
        "id": f"{guest_type}/{form['vmid']}",
        "node": node,
        "type": guest_type,
        "vmid": int(form["vmid"]),
        "name": form.get("hostname") or form["name"],
        "status": "stopped",
        "maxcpu": int(form["cores"]),
        "maxmem": int(form["memory"]) * 1024 * 1024,
    }


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

    @property
    def write_requests(self) -> list[RecordedRequest]:
        """Every request that was not a GET, including those answered 405."""
        return [r for r in self.state.requests if r.method != "GET"]

    @property
    def write_classes(self) -> list[tuple[str, str]]:
        """Every write as (class, path), the class by ``classify_write``."""
        return [(classify_write(r), r.path) for r in self.write_requests]
