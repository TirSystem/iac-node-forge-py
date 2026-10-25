"""The mocks accept only the power writes, the creations, the removals and the
backup and restore writes.

The allow-list is exactly the four power POSTs, the Proxmox VE lxc and qemu
creations, the Contabo instance creation (and its token POST), the Proxmox VE
DELETE of an lxc or qemu resource, the Contabo cancel POST, and (ADR-0010) the
Proxmox VE vzdump POST, the Proxmox VE restore (a POST to the creation path with
``force``) and the Contabo snapshot POST and rollback POST. This is the
receiving side of Go criterion 5 of MIL-004, MIL-006 and MIL-008 and the
write-surface rule of ADR-0007, ADR-0008 and ADR-0010: the recorders prove what
the adapters sent, and these tests prove that anything else would have been
refused and recorded rather than silently served. A restore uses the path of a
creation, so the recorder tells them apart by the body.
"""

import httpx
import pytest

from tests.contract.conftest import TOKEN
from tests.contract.mock_contabo import MockContaboServer
from tests.contract.mock_proxmox import (
    MockProxmoxServer,
    RecordedRequest,
    classify_write,
)

PVE = "/api2/json/nodes/pve1"
BEARER = {
    "Authorization": "Bearer token",
    "x-request-id": "5f8a1c0e-4d3b-4a7e-9c21-0b6d2e7f1a34",
}

PROXMOX_FORBIDDEN = [
    ("POST", "/api2/json/nodes/pve1/qemu/100/clone"),
    ("POST", "/api2/json/nodes/pve1/lxc/101"),
    ("POST", "/api2/json/nodes/pve1/qemu/100/resize"),
    ("PUT", "/api2/json/nodes/pve1/qemu/100/resize"),
    ("POST", "/api2/json/nodes/pve1/qemu/100/template"),
    ("POST", "/api2/json/nodes/pve1/lxc/101/extra/more"),
    ("POST", "/api2/json/nodes/pve1/vzdump/extra"),
    ("PUT", "/api2/json/nodes/pve1/vzdump"),
    ("DELETE", "/api2/json/nodes/pve1/vzdump"),
    ("DELETE", "/api2/json/nodes/pve1/storage/local/content/local:backup/x.tar"),
    ("DELETE", "/api2/json/nodes/pve1/storage/local/content"),
    ("POST", "/api2/json/nodes/pve1/storage/local/download-url"),
    ("POST", "/api2/json/nodes/pve1/qemu/100/snapshot/before/rollback"),
    ("DELETE", "/api2/json/nodes/pve1/lxc/101/snapshot/before"),
    ("POST", "/api2/json/nodes/pve1/storage"),
    ("PUT", "/api2/json/nodes/pve1/qemu"),
    ("DELETE", "/api2/json/nodes/pve1/lxc"),
    ("DELETE", "/api2/json/nodes/pve1/lxc/abc"),
    ("DELETE", "/api2/json/nodes/pve1/lxc/101/config"),
    ("DELETE", "/api2/json/nodes/pve1/qemu/100/snapshot/before"),
    ("DELETE", "/api2/json/cluster/resources"),
    ("DELETE", "/api2/json/nodes/pve1/storage/local"),
    ("POST", "/api2/json/nodes/pve1/qemu/100/status/suspend"),
    ("POST", "/api2/json/nodes/pve1/qemu/100/status/reset"),
    ("POST", "/api2/json/nodes/pve1/qemu/100/snapshot"),
    ("POST", "/api2/json/nodes/pve1/qemu/100/migrate"),
    ("POST", "/api2/json/nodes/pve1/status/shutdown"),
    ("POST", "/api2/json/nodes/pve1/qemu/100/status/start/extra"),
    ("PUT", "/api2/json/nodes/pve1/qemu/100/status/start"),
    ("PATCH", "/api2/json/nodes/pve1/qemu/100/config"),
    ("DELETE", "/api2/json/nodes/pve1/qemu/100/status/stop"),
]

CONTABO_FORBIDDEN = [
    ("POST", "/v1/compute/instances/"),
    ("POST", "/v1/compute/instances/100"),
    ("POST", "/v1/compute/images"),
    ("POST", "/v1/compute/products"),
    ("PUT", "/v1/compute/instances"),
    ("PATCH", "/v1/compute/instances"),
    ("DELETE", "/v1/compute/instances"),
    ("POST", "/v1/compute/instances/100/actions/rescue"),
    ("POST", "/v1/compute/instances/100/actions/reinstall"),
    ("POST", "/v1/compute/instances/100/cancel/extra"),
    ("POST", "/v1/compute/instances/abc/cancel"),
    ("PUT", "/v1/compute/instances/100/cancel"),
    ("DELETE", "/v1/compute/instances/100/cancel"),
    ("POST", "/v1/compute/instances/abc/snapshots"),
    ("POST", "/v1/compute/instances/100/snapshots/extra"),
    ("PUT", "/v1/compute/instances/100/snapshots"),
    ("DELETE", "/v1/compute/instances/100/snapshots"),
    ("DELETE", "/v1/compute/instances/100/snapshots/abc"),
    ("PATCH", "/v1/compute/instances/100/snapshots/abc"),
    ("POST", "/v1/compute/instances/100/snapshots/abc"),
    ("PUT", "/v1/compute/instances/100/snapshots/abc/rollback"),
    ("DELETE", "/v1/compute/instances/100/snapshots/abc/rollback"),
    ("POST", "/v1/compute/instances/100/snapshots/abc/rollback/extra"),
    ("POST", "/v1/compute/instances/abc/snapshots/s1/rollback"),
    ("POST", "/v1/compute/snapshots/s1/rollback"),
    ("POST", "/v1/compute/instances/100/backup"),
    ("POST", "/v1/compute/instances/100/actions/resize"),
    ("POST", "/v1/compute/instances/100/upgrade"),
    ("POST", "/v1/compute/instances/100/actions/start/extra"),
    ("PUT", "/v1/compute/instances/100"),
    ("PUT", "/v1/compute/instances/100/actions/start"),
    ("PATCH", "/v1/compute/instances/100"),
    ("DELETE", "/v1/compute/instances/100"),
    ("DELETE", "/v1/compute/instances/100/actions/stop"),
]


@pytest.mark.parametrize(("method", "path"), PROXMOX_FORBIDDEN)
def test_proxmox_mock_refuses_and_records_every_write_outside_the_allow_list(
    mock_proxmox: MockProxmoxServer, method: str, path: str
) -> None:
    response = httpx.request(
        method,
        mock_proxmox.address + path,
        headers={"Authorization": f"PVEAPIToken={TOKEN}"},
    )

    assert response.status_code == 405
    assert [(r.method, r.path) for r in mock_proxmox.write_requests] == [(method, path)]
    assert mock_proxmox.state.tasks == {}
    assert [r["status"] for r in mock_proxmox.state.resources] == [
        "running",
        "stopped",
    ]


@pytest.mark.parametrize(("method", "path"), CONTABO_FORBIDDEN)
def test_contabo_mock_refuses_and_records_every_write_outside_the_allow_list(
    mock_contabo: MockContaboServer, method: str, path: str
) -> None:
    response = httpx.request(method, mock_contabo.address + path)

    assert response.status_code == 405
    assert [(r.method, r.path) for r in mock_contabo.write_requests] == [(method, path)]
    assert mock_contabo.state.transitions == {}
    assert [i["status"] for i in mock_contabo.state.instances] == [
        "running",
        "stopped",
    ]


def test_proxmox_mock_without_a_token_does_not_start_a_task(
    mock_proxmox: MockProxmoxServer,
) -> None:
    response = httpx.post(
        mock_proxmox.address + "/api2/json/nodes/pve1/qemu/100/status/stop"
    )

    assert response.status_code == 401
    assert mock_proxmox.state.tasks == {}


@pytest.mark.parametrize("guest_type", ["lxc", "qemu"])
def test_proxmox_mock_accepts_and_records_the_two_creations_only_with_a_token(
    mock_proxmox: MockProxmoxServer, guest_type: str
) -> None:
    path = f"/api2/json/nodes/pve1/{guest_type}"

    refused = httpx.post(mock_proxmox.address + path, data={"vmid": "102"})

    assert refused.status_code == 401
    assert mock_proxmox.state.tasks == {}
    assert mock_proxmox.state.created == []
    assert [(r.method, r.path) for r in mock_proxmox.write_requests] == [("POST", path)]


def test_proxmox_mock_refuses_a_creation_with_a_taken_identifier() -> None:
    with MockProxmoxServer(TOKEN) as server:
        server.state.nodes = [{"node": "pve1"}]
        server.state.resources = [{"node": "pve1", "vmid": 102}]

        response = httpx.post(
            server.address + "/api2/json/nodes/pve1/lxc",
            headers={"Authorization": f"PVEAPIToken={TOKEN}"},
            data={
                "vmid": "102",
                "hostname": "x",
                "ostemplate": "local:vztmpl/x",
                "rootfs": "local:1",
                "memory": "512",
                "cores": "1",
                "net0": "name=eth0,bridge=vmbr0,ip=dhcp",
            },
        )

        assert response.status_code == 500
        assert server.state.tasks == {}


def test_contabo_mock_accepts_the_creation_only_with_a_token(
    mock_contabo: MockContaboServer,
) -> None:
    response = httpx.post(mock_contabo.address + "/v1/compute/instances", json={})

    assert response.status_code == 401
    assert mock_contabo.state.created == []
    assert len(mock_contabo.state.instances) == 2


@pytest.mark.parametrize("guest_type", ["lxc", "qemu"])
def test_proxmox_mock_accepts_and_records_the_deletion_only_with_a_token(
    mock_proxmox: MockProxmoxServer, guest_type: str
) -> None:
    path = f"/api2/json/nodes/pve1/{guest_type}/100"

    refused = httpx.delete(mock_proxmox.address + path)

    assert refused.status_code == 401
    assert mock_proxmox.state.tasks == {}
    assert mock_proxmox.state.deleted == []
    assert [(r.method, r.path) for r in mock_proxmox.write_requests] == [
        ("DELETE", path)
    ]


def test_proxmox_mock_deletes_a_stopped_guest_through_a_task() -> None:
    with MockProxmoxServer(TOKEN) as server:
        server.state.resources = [
            {"node": "pve1", "type": "lxc", "vmid": 101, "status": "stopped"}
        ]

        response = httpx.delete(
            server.address
            + "/api2/json/nodes/pve1/lxc/101?purge=1&destroy-unreferenced-disks=1",
            headers={"Authorization": f"PVEAPIToken={TOKEN}"},
        )

        assert response.status_code == 200
        assert response.json()["data"].startswith("UPID:pve1:")
        assert server.state.deleted == [
            {"purge": "1", "destroy-unreferenced-disks": "1"}
        ]
        assert len(server.state.resources) == 1


def test_proxmox_mock_refuses_the_deletion_of_a_running_guest_as_the_provider_does(
    mock_proxmox: MockProxmoxServer,
) -> None:
    response = httpx.delete(
        mock_proxmox.address + "/api2/json/nodes/pve1/qemu/100",
        headers={"Authorization": f"PVEAPIToken={TOKEN}"},
    )

    assert response.status_code == 500
    assert mock_proxmox.state.tasks == {}
    assert mock_proxmox.state.resources[0]["status"] == "running"


def test_proxmox_mock_allow_list_is_exactly_the_documented_writes(
    mock_proxmox: MockProxmoxServer,
) -> None:
    headers = {"Authorization": f"PVEAPIToken={TOKEN}"}
    allowed = [
        ("POST", "/api2/json/nodes/pve1/qemu/100/status/start"),
        ("POST", "/api2/json/nodes/pve1/qemu/100/status/shutdown"),
        ("POST", "/api2/json/nodes/pve1/qemu/100/status/stop"),
        ("POST", "/api2/json/nodes/pve1/qemu/100/status/reboot"),
        ("POST", "/api2/json/nodes/pve1/lxc"),
        ("POST", "/api2/json/nodes/pve1/qemu"),
        ("POST", "/api2/json/nodes/pve1/vzdump"),
        ("DELETE", "/api2/json/nodes/pve2/lxc/101"),
        ("DELETE", "/api2/json/nodes/pve1/qemu/100"),
    ]

    answers = [
        httpx.request(method, mock_proxmox.address + path, headers=headers)
        for method, path in allowed
    ]

    assert 405 not in {answer.status_code for answer in answers}


def test_contabo_mock_accepts_the_cancellation_only_with_a_token(
    mock_contabo: MockContaboServer,
) -> None:
    response = httpx.post(mock_contabo.address + "/v1/compute/instances/101/cancel")

    assert response.status_code == 401
    assert mock_contabo.state.cancelled == []
    assert mock_contabo.state.transitions == {}
    assert [i["status"] for i in mock_contabo.state.instances] == [
        "running",
        "stopped",
    ]


def test_contabo_mock_cancel_of_an_unknown_instance_is_not_found(
    mock_contabo: MockContaboServer,
) -> None:
    mock_contabo.state.current_token = "token"

    response = httpx.post(
        mock_contabo.address + "/v1/compute/instances/999/cancel",
        headers={
            "Authorization": "Bearer token",
            "x-request-id": "5f8a1c0e-4d3b-4a7e-9c21-0b6d2e7f1a34",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 404
    assert mock_contabo.state.cancelled == []


LXC_RESTORE = {
    "vmid": "101",
    "ostemplate": "local:backup/vzdump-lxc-101-x.tar",
    "restore": "1",
    "force": "1",
}
QEMU_RESTORE = {
    "vmid": "100",
    "archive": "local:backup/vzdump-qemu-100-x.vma",
    "force": "1",
}
DUMP = {
    "vmid": "100",
    "storage": "local",
    "mode": "snapshot",
    "remove": "0",
    "notes-template": "iac-node-forge op {{guestname}}",
}
CREATION = {"vmid": "102", "hostname": "x", "memory": "512", "cores": "1"}

CLASS_CASES = [
    ("POST", f"{PVE}/lxc", {}, "create"),
    ("POST", f"{PVE}/lxc", CREATION, "create"),
    ("POST", f"{PVE}/qemu", {"vmid": "102", "name": "x", "scsi0": "y"}, "create"),
    ("POST", f"{PVE}/lxc", LXC_RESTORE, "restore"),
    ("POST", f"{PVE}/qemu", QEMU_RESTORE, "restore"),
    ("POST", f"{PVE}/vzdump", DUMP, "vzdump"),
    ("POST", f"{PVE}/qemu/100/status/stop", {}, "power"),
    ("DELETE", f"{PVE}/lxc/101", {}, "delete"),
    # A body with a restore field that is not exactly a restore is not listed.
    ("POST", f"{PVE}/lxc", {"force": "1"}, "unlisted"),
    ("POST", f"{PVE}/lxc", {"vmid": "101", "restore": "1"}, "unlisted"),
    ("POST", f"{PVE}/lxc", {"archive": "x"}, "unlisted"),
    ("POST", f"{PVE}/lxc", {**LXC_RESTORE, "force": "0"}, "unlisted"),
    ("POST", f"{PVE}/lxc", {**LXC_RESTORE, "archive": "x"}, "unlisted"),
    ("POST", f"{PVE}/lxc", {**LXC_RESTORE, "restore": "0"}, "unlisted"),
    ("POST", f"{PVE}/lxc", {"vmid": "101", "restore": "1", "force": "1"}, "unlisted"),
    ("POST", f"{PVE}/lxc", QEMU_RESTORE, "unlisted"),
    ("POST", f"{PVE}/qemu", LXC_RESTORE, "unlisted"),
    ("POST", f"{PVE}/qemu", {"vmid": "100", "archive": "x"}, "unlisted"),
    ("POST", f"{PVE}/qemu", {"vmid": "100", "force": "1"}, "unlisted"),
    ("POST", f"{PVE}/qemu", {**QEMU_RESTORE, "restore": "1"}, "unlisted"),
    ("POST", f"{PVE}/qemu", {**QEMU_RESTORE, "ostemplate": "x"}, "unlisted"),
    ("POST", f"{PVE}/qemu", {**QEMU_RESTORE, "force": "true"}, "unlisted"),
    ("POST", f"{PVE}/clone", {}, "unlisted"),
    ("POST", f"{PVE}/vzdump/extra", {}, "unlisted"),
    ("PUT", f"{PVE}/lxc", LXC_RESTORE, "unlisted"),
    ("DELETE", f"{PVE}/storage/local/content/local:backup/x", {}, "unlisted"),
    ("DELETE", f"{PVE}/lxc", {}, "unlisted"),
]


@pytest.mark.parametrize(("method", "path", "form", "expected"), CLASS_CASES)
def test_the_recorder_tells_a_creation_from_a_restore_by_the_body(
    method: str, path: str, form: dict[str, str], expected: str
) -> None:
    assert classify_write(RecordedRequest(method, path, form)) == expected


def authorised(address: str, path: str, form: dict[str, str]) -> httpx.Response:
    return httpx.post(
        address + "/api2/json" + path.removeprefix("/api2/json"),
        headers={"Authorization": f"PVEAPIToken={TOKEN}"},
        data=form,
    )


def poll(address: str, upid: str, times: int = 5) -> dict[str, str]:
    status: dict[str, str] = {}
    for _ in range(times):
        status = httpx.get(
            f"{address}/api2/json/nodes/pve1/tasks/{upid}/status",
            headers={"Authorization": f"PVEAPIToken={TOKEN}"},
        ).json()["data"]
        if status["status"] == "stopped":
            break
    return status


def test_proxmox_mock_without_a_token_does_not_start_a_dump_or_a_restore(
    mock_proxmox: MockProxmoxServer,
) -> None:
    dump = httpx.post(mock_proxmox.address + f"{PVE}/vzdump", data=DUMP)
    restore = httpx.post(mock_proxmox.address + f"{PVE}/qemu", data=QEMU_RESTORE)

    assert (dump.status_code, restore.status_code) == (401, 401)
    assert mock_proxmox.state.tasks == {}
    assert mock_proxmox.state.dumped == mock_proxmox.state.restored == []
    assert [c for c, _ in mock_proxmox.write_classes] == ["vzdump", "restore"]


def test_proxmox_mock_dumps_through_a_task_and_lists_the_archive_by_filter(
    mock_proxmox: MockProxmoxServer,
) -> None:
    answer = authorised(mock_proxmox.address, f"{PVE}/vzdump", DUMP)
    upid = answer.json()["data"]
    listing = f"{mock_proxmox.address}/api2/json/nodes/pve1/storage/local/content"
    headers = {"Authorization": f"PVEAPIToken={TOKEN}"}

    before = httpx.get(listing + "?content=backup&vmid=100", headers=headers)
    status = poll(mock_proxmox.address, upid)
    after = httpx.get(listing + "?content=backup&vmid=100", headers=headers)
    other_guest = httpx.get(listing + "?content=backup&vmid=101", headers=headers)
    other_content = httpx.get(listing + "?content=iso&vmid=100", headers=headers)

    assert answer.status_code == 200 and upid.startswith("UPID:pve1:")
    assert before.json()["data"] == []
    assert status["exitstatus"] == "OK"
    (archive,) = after.json()["data"]
    assert archive["volid"].startswith("local:backup/vzdump-qemu-100-")
    assert archive["notes"] == "iac-node-forge op web"
    assert archive["size"] > 0 and archive["ctime"] > 0
    assert other_guest.json()["data"] == []
    assert other_content.json()["data"] == []


@pytest.mark.parametrize(
    "form",
    [
        {**DUMP, "storage": "local-lvm"},
        {**DUMP, "storage": "ghost"},
        {**DUMP, "vmid": "999"},
        {key: value for key, value in DUMP.items() if key != "notes-template"},
    ],
)
def test_proxmox_mock_refuses_a_dump_it_would_not_carry_out(
    mock_proxmox: MockProxmoxServer, form: dict[str, str]
) -> None:
    response = authorised(mock_proxmox.address, f"{PVE}/vzdump", form)

    assert response.status_code == 500
    assert mock_proxmox.state.tasks == {}
    assert mock_proxmox.state.dumped == []


def test_proxmox_mock_restores_an_archive_over_a_stopped_guest_only_with_force() -> (
    None
):
    with MockProxmoxServer(TOKEN) as server:
        server.state.nodes = [{"node": "pve1"}]
        server.state.storages = {"pve1": [{"storage": "local", "content": "backup"}]}
        server.state.resources = [
            {
                "node": "pve1",
                "type": "qemu",
                "vmid": 100,
                "name": "web",
                "status": "stopped",
            }
        ]
        dump = authorised(server.address, f"{PVE}/vzdump", DUMP).json()["data"]
        poll(server.address, dump)
        volid = server.state.storage_content["pve1/local"][0]["volid"]
        server.state.resources[0]["name"] = "changed"
        form = {"vmid": "100", "archive": volid}

        without_force = authorised(server.address, f"{PVE}/qemu", form)
        forced = authorised(server.address, f"{PVE}/qemu", {**form, "force": "1"})
        status = poll(server.address, forced.json()["data"])

        assert without_force.status_code == 500
        assert forced.status_code == 200
        assert status["exitstatus"] == "OK"
        assert server.state.resources[0]["name"] == "web"
        assert [c for c, _ in server.write_classes] == ["vzdump", "unlisted", "restore"]


def test_proxmox_mock_refuses_a_restore_over_a_running_guest_or_of_an_unknown_archive(
    mock_proxmox: MockProxmoxServer,
) -> None:
    dump = authorised(mock_proxmox.address, f"{PVE}/vzdump", DUMP).json()["data"]
    poll(mock_proxmox.address, dump)
    volid = mock_proxmox.state.storage_content["pve1/local"][0]["volid"]

    running = authorised(
        mock_proxmox.address, f"{PVE}/qemu", {**QEMU_RESTORE, "archive": volid}
    )
    unknown = authorised(mock_proxmox.address, f"{PVE}/qemu", QEMU_RESTORE)

    assert (running.status_code, unknown.status_code) == (500, 500)
    assert mock_proxmox.state.restored == []
    assert mock_proxmox.state.resources[0]["status"] == "running"


def contabo_post(
    server: MockContaboServer, path: str, json: object | None = None
) -> httpx.Response:
    server.state.current_token = "token"
    return httpx.post(
        server.address + path,
        headers={**BEARER, "Content-Type": "application/json"},
        json=json,
        content=None if json is not None else b"",
    )


def test_contabo_mock_accepts_the_snapshot_and_the_rollback_only_with_a_token(
    mock_contabo: MockContaboServer,
) -> None:
    snapshot = httpx.post(
        mock_contabo.address + "/v1/compute/instances/101/snapshots",
        json={"name": "ok", "description": "d"},
    )
    rollback = httpx.post(
        mock_contabo.address + "/v1/compute/instances/101/snapshots/s1/rollback"
    )

    assert (snapshot.status_code, rollback.status_code) == (401, 401)
    assert mock_contabo.state.snapshots == []
    assert mock_contabo.state.snapshot_requests == []
    assert mock_contabo.state.rolled_back == []
    assert mock_contabo.state.transitions == {}


@pytest.mark.parametrize(
    "body",
    [
        {"name": "n" * 31, "description": "d"},
        {"name": "", "description": "d"},
        {"name": "bad_name!", "description": "d"},
        {"name": "ok", "description": ""},
        {"name": "ok", "description": "d" * 256},
        {"name": "ok"},
        {"description": "d"},
    ],
)
def test_contabo_mock_refuses_a_snapshot_outside_the_documented_limits(
    mock_contabo: MockContaboServer, body: dict[str, str]
) -> None:
    response = contabo_post(mock_contabo, "/v1/compute/instances/101/snapshots", body)

    assert response.status_code == 400
    assert mock_contabo.state.snapshots == []


def test_contabo_mock_refuses_a_snapshot_of_an_unknown_instance(
    mock_contabo: MockContaboServer,
) -> None:
    body = {"name": "ok", "description": "d"}

    response = contabo_post(mock_contabo, "/v1/compute/instances/999/snapshots", body)

    assert response.status_code == 404
    assert mock_contabo.state.snapshots == []


def test_contabo_mock_shows_a_new_snapshot_only_after_the_instance_settled() -> None:
    with MockContaboServer("c", "s", "u", "p") as server:
        server.state.instances = [
            {"instanceId": 101, "status": "stopped", "displayName": "db"}
        ]
        server.state.transition_reads = 1
        answer = contabo_post(
            server,
            "/v1/compute/instances/101/snapshots",
            {"name": "ok", "description": "d"},
        )
        listing = f"{server.address}/v1/compute/instances/101/snapshots"
        headers = BEARER

        hidden = httpx.get(listing, headers=headers).json()["data"]
        httpx.get(server.address + "/v1/compute/instances/101", headers=headers)
        httpx.get(server.address + "/v1/compute/instances/101", headers=headers)
        shown = httpx.get(listing, headers=headers).json()["data"]

        assert answer.status_code == 201
        assert hidden == []
        assert [item["name"] for item in shown] == ["ok"]
        assert shown[0]["snapshotId"] == answer.json()["data"][0]["snapshotId"]


def test_contabo_mock_rolls_back_to_a_snapshot_and_deletes_the_newer_ones() -> None:
    with MockContaboServer("c", "s", "u", "p") as server:
        server.state.instances = [
            {"instanceId": 101, "status": "running", "displayName": "db"}
        ]
        server.state.transition_reads = 0
        ids = []
        for name in ("first", "second", "third"):
            answer = contabo_post(
                server,
                "/v1/compute/instances/101/snapshots",
                {"name": name, "description": "d"},
            )
            ids.append(answer.json()["data"][0]["snapshotId"])
            httpx.get(server.address + "/v1/compute/instances/101", headers=BEARER)
        path = f"/v1/compute/instances/101/snapshots/{ids[1]}/rollback"

        answer = contabo_post(server, path)
        httpx.get(server.address + "/v1/compute/instances/101", headers=BEARER)
        left = httpx.get(
            server.address + "/v1/compute/instances/101/snapshots", headers=BEARER
        ).json()["data"]

        assert answer.status_code == 200
        assert [item["name"] for item in left] == ["first", "second"]
        assert server.state.rolled_back == [("101", ids[1])]


def test_contabo_mock_refuses_a_rollback_to_an_unknown_or_foreign_snapshot(
    mock_contabo: MockContaboServer,
) -> None:
    mock_contabo.state.snapshots.append(
        {"snapshotId": "other", "instanceId": 100, "name": "x", "createdDate": "z"}
    )

    unknown = contabo_post(
        mock_contabo, "/v1/compute/instances/101/snapshots/ghost/rollback"
    )
    foreign = contabo_post(
        mock_contabo, "/v1/compute/instances/101/snapshots/other/rollback"
    )

    assert (unknown.status_code, foreign.status_code) == (404, 404)
    assert mock_contabo.state.rolled_back == []
    assert mock_contabo.state.transitions == {}


def test_contabo_mock_recorder_lists_the_snapshot_and_rollback_posts(
    mock_contabo: MockContaboServer,
) -> None:
    contabo_post(
        mock_contabo,
        "/v1/compute/instances/101/snapshots",
        {"name": "ok", "description": "d"},
    )
    contabo_post(mock_contabo, "/v1/compute/instances/101/snapshots/s/rollback")

    assert [(r.method, r.path) for r in mock_contabo.write_requests] == [
        ("POST", "/v1/compute/instances/101/snapshots"),
        ("POST", "/v1/compute/instances/101/snapshots/s/rollback"),
    ]
