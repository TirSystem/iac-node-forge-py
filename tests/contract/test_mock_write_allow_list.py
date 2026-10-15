"""The mocks accept only the four power writes; every other write answers 405.

This is the receiving side of Go criterion 5 of MIL-004: the recorders prove
what the adapters sent, and these tests prove that anything else would have
been refused and recorded rather than silently served.
"""

import httpx
import pytest

from tests.contract.conftest import TOKEN
from tests.contract.mock_contabo import MockContaboServer
from tests.contract.mock_proxmox import MockProxmoxServer

PROXMOX_FORBIDDEN = [
    ("POST", "/api2/json/nodes/pve1/qemu"),
    ("POST", "/api2/json/nodes/pve1/qemu/100/status/suspend"),
    ("POST", "/api2/json/nodes/pve1/qemu/100/status/reset"),
    ("POST", "/api2/json/nodes/pve1/qemu/100/snapshot"),
    ("POST", "/api2/json/nodes/pve1/qemu/100/migrate"),
    ("POST", "/api2/json/nodes/pve1/status/shutdown"),
    ("POST", "/api2/json/nodes/pve1/qemu/100/status/start/extra"),
    ("PUT", "/api2/json/nodes/pve1/qemu/100/status/start"),
    ("PATCH", "/api2/json/nodes/pve1/qemu/100/config"),
    ("DELETE", "/api2/json/nodes/pve1/qemu/100"),
    ("DELETE", "/api2/json/nodes/pve1/qemu/100/status/stop"),
]

CONTABO_FORBIDDEN = [
    ("POST", "/v1/compute/instances"),
    ("POST", "/v1/compute/instances/100/actions/rescue"),
    ("POST", "/v1/compute/instances/100/actions/reinstall"),
    ("POST", "/v1/compute/instances/100/cancel"),
    ("POST", "/v1/compute/instances/100/snapshots"),
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
