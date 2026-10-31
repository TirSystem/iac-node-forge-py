"""Live full backup and restore test: it CREATES A CONTAINER, backs it up, changes
it, restores it, then removes it and its backup archive.

Only Proxmox VE is tested live, and only the disposable Docker Proxmox VE of
``docker-compose.proxmox.yml``. Contabo is never touched: a Contabo snapshot
costs money and a rollback deletes the newer snapshots.

It needs the variables of ``test_provision_live.py`` (``PVE_TOKEN``,
``PVE_PROVISION_TEST_NODE``, ``PVE_PROVISION_TEST_STORAGE``,
``PVE_PROVISION_TEST_TEMPLATE`` and ``PVE_PROVISION_TEST_CONFIRM`` equal to
``create-server-iacnf-live``). The storage of the backup is the only storage of
the node that accepts backups, or the one named by the optional
``PVE_PROVISION_TEST_BACKUP_STORAGE``; with none, the test fails and says to
enable the ``backup`` content on a storage. With ``PVE_PROVISION_TEST_EXPERIMENT=1``
it also starts the container and records, as printed notes and not as
assertions, how a restore over a running guest is answered.

Every step goes through the product (``BackUpServer``, ``ListBackups``,
``RestoreServer``, ``ControlServerPower``, ``RemoveServer``) and the real
adapter and JSON Lines stores. The test sends three kinds of request itself,
marked ``X-Iacnf-Test-Only`` so they are counted apart from the product's:
``PUT .../lxc/{vmid}/config`` to change the description so that a restore is
detectable, ``DELETE .../storage/{storage}/content/{volid}`` to delete the
archive this run made (so the Docker Proxmox VE does not fill up), and, in the
experiment only, one direct ``POST .../lxc`` restore over the running guest.
They are limited to this run's vmid and volume. A finalizer removes the archives
of this run's vmid, and the container, also when a step fails. No credential
value is printed, logged or put in an assertion message.
"""

import os
import ssl
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from iac_node_forge.application.backup_dto import (
    BackupOutcome,
    RestoreOutcome,
)
from iac_node_forge.application.dto import Confirmation, PowerOutcome
from iac_node_forge.application.use_cases.back_up_server import BackUpServer
from iac_node_forge.application.use_cases.control_server_power import (
    ControlServerPower,
)
from iac_node_forge.application.use_cases.list_backups import ListBackups
from iac_node_forge.application.use_cases.restore_server import RestoreServer
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.operation import OperationId, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.infrastructure.configuration import (
    JsonLinesBackupStore,
    JsonLinesConfigurationStore,
    JsonLinesOperationStore,
)
from tests.integration._live_proxmox import (
    STOPPED,
    TEST_ONLY_HEADER,
    TIMEOUT_SECONDS,
    FixedResolver,
    ProvisionTarget,
    WriteLog,
    create_container,
    live_target,
    record_writes,
    remove_through_product,
    tracker_of,
    verify_from_environment,
)

pytestmark = pytest.mark.integration

BACKUP_STORAGE_VARIABLE = "PVE_PROVISION_TEST_BACKUP_STORAGE"
EXPERIMENT_VARIABLE = "PVE_PROVISION_TEST_EXPERIMENT"
API_PREFIX = "/api2/json"
TASK_WAIT_SECONDS = 120.0
TASK_POLL_SECONDS = 2.0
NOTE_LIMIT = 400
UPID_PREFIX = "UPID:"
NO_STORAGE_MESSAGE = (
    "no storage of the node accepts backups: enable the 'backup' content on a "
    "storage of the disposable Proxmox VE (Datacenter, Storage, Edit, Content) or "
    f"name one with {BACKUP_STORAGE_VARIABLE}"
)


@dataclass(slots=True)
class Scene:
    """What this run made on the Proxmox VE, so a finalizer can clean it up."""

    vmid: str | None = None
    storages: set[str] = field(default_factory=set)


@pytest.fixture(scope="module")
def target(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ProvisionTarget]:
    yield from live_target(tmp_path_factory)


@pytest.fixture
def write_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[WriteLog]:
    yield from record_writes(monkeypatch)


@pytest.fixture
def scene(target: ProvisionTarget) -> Iterator[Scene]:
    """Delete the archives of this run's vmid after the test, even on failure."""
    made = Scene()
    try:
        yield made
    finally:
        if made.vmid is not None:
            for storage in sorted(made.storages):
                for volid in _archives_of(target, storage, made.vmid):
                    _delete_archive(target, storage, made.vmid, volid)


# --- Test-only requests: the only raw writes, documented in the module docstring.


def _verify() -> bool | ssl.SSLContext:
    verify = verify_from_environment()
    if isinstance(verify, str):
        return ssl.create_default_context(cafile=verify)
    return verify


def _api(
    target: ProvisionTarget,
    method: str,
    path: str,
    form: dict[str, str] | None = None,
) -> httpx.Response:
    """Send one request with the token of the run; mark it as sent by the test."""
    token = os.environ["PVE_TOKEN"].strip()
    url = target.configuration.address.rstrip("/") + API_PREFIX + path
    headers = {"Authorization": f"PVEAPIToken={token}", TEST_ONLY_HEADER: "1"}
    with httpx.Client(verify=_verify(), timeout=TIMEOUT_SECONDS) as client:
        return client.request(method, url, headers=headers, data=form)


def _data(response: httpx.Response) -> Any:  # noqa: ANN401 - a JSON value
    assert response.status_code == 200, f"status {response.status_code}"
    return response.json()["data"]


def _config_path(target: ProvisionTarget, vmid: str) -> str:
    return f"/nodes/{quote(target.node, safe='')}/lxc/{quote(vmid, safe='')}/config"


def _description(target: ProvisionTarget, vmid: str) -> str | None:
    """Read the description of the container back from its configuration."""
    config = _data(_api(target, "GET", _config_path(target, vmid)))
    value = config.get("description")
    return None if value is None else str(value).strip()


def _change_description(target: ProvisionTarget, vmid: str, text: str) -> None:
    """TEST-ONLY write: PUT the config with a new description."""
    response = _api(target, "PUT", _config_path(target, vmid), {"description": text})
    assert response.status_code == 200, f"config change status {response.status_code}"


def _wait_for_task(target: ProvisionTarget, upid: str) -> str:
    """Wait for a task the test started itself; return its exit status."""
    path = f"/nodes/{quote(target.node, safe='')}/tasks/{quote(upid, safe='')}/status"
    deadline = time.monotonic() + TASK_WAIT_SECONDS
    while True:
        status = _data(_api(target, "GET", path))
        if status.get("status") == STOPPED:
            return str(status.get("exitstatus", ""))
        assert time.monotonic() < deadline, "a test-only task did not end in time"
        time.sleep(TASK_POLL_SECONDS)


def _archives_of(target: ProvisionTarget, storage: str, vmid: str) -> list[str]:
    """List the backup archives of this run's vmid on one storage."""
    path = (
        f"/nodes/{quote(target.node, safe='')}/storage/{quote(storage, safe='')}"
        f"/content?content=backup&vmid={quote(vmid, safe='')}"
    )
    return [str(item["volid"]) for item in _data(_api(target, "GET", path))]


def _delete_archive(
    target: ProvisionTarget, storage: str, vmid: str, volid: str
) -> None:
    """TEST-ONLY write: DELETE the archive this run made; nothing else."""
    assert volid.startswith(f"{storage}:backup/"), "not an archive of this storage"
    assert f"-{vmid}-" in volid, "not an archive of this run's guest"
    path = (
        f"/nodes/{quote(target.node, safe='')}/storage/{quote(storage, safe='')}"
        f"/content/{quote(volid, safe='')}"
    )
    response = _api(target, "DELETE", path)
    assert response.status_code == 200, (
        f"archive deletion status {response.status_code}"
    )
    answer = response.json().get("data")
    if isinstance(answer, str) and answer.startswith(UPID_PREFIX):
        assert _wait_for_task(target, answer) == "OK"


# --- The steps of the scenario.


def _short(text: str) -> str:
    return text if len(text) <= NOTE_LIMIT else text[:NOTE_LIMIT] + "..."


def _experiment(
    target: ProvisionTarget,
    vmid: str,
    volid: str,
    log: WriteLog,
    seen: list[str],
) -> None:
    """Record how a restore over a running guest is answered; assert little."""
    resolver = FixedResolver(target.provider)
    configurations = JsonLinesConfigurationStore(target.store_path)
    operations = JsonLinesOperationStore(target.store_path)
    configuration_id = target.configuration.configuration_id
    power = ControlServerPower(resolver, configurations, operations)
    restorer = RestoreServer(resolver, configurations, operations)
    tracker = tracker_of(target)
    notes: list[str] = []

    started = power.request_power_action(
        configuration_id, vmid, PowerAction.START, Confirmation(given=True)
    )
    assert started.outcome is PowerOutcome.REQUESTED, started.reason
    assert started.operation is not None
    tracker.follow_operation(started.operation.operation_id)
    try:
        before = len(log.product)
        refused = restorer.request_restore(
            configuration_id, vmid, volid, Confirmation(True, vmid)
        )
        notes.append(f"product restore of a running guest: {refused.outcome.value}")
        notes.append(f"product reason: {_short(refused.reason)}")
        assert refused.outcome is RestoreOutcome.RUNNING_REFUSED
        assert len(log.product) == before
        notes.append(_direct_restore_over_running(target, vmid, volid))
    finally:
        stopped = power.request_power_action(
            configuration_id, vmid, PowerAction.STOP, Confirmation(given=True)
        )
        assert stopped.operation is not None
        ended = tracker.follow_operation(stopped.operation.operation_id)
        assert ended.state is OperationState.SUCCEEDED, ended.detail
    for note in notes:
        print(f"EXPERIMENT NOTE: {note}")
    seen.extend(notes)


def _direct_restore_over_running(target: ProvisionTarget, vmid: str, volid: str) -> str:
    """TEST-ONLY write: ask Proxmox VE to restore over the running guest."""
    path = f"/nodes/{quote(target.node, safe='')}/lxc"
    response = _api(
        target,
        "POST",
        path,
        {"vmid": vmid, "ostemplate": volid, "restore": "1", "force": "1"},
    )
    note = f"direct restore over a running guest: status {response.status_code}, "
    note += f"answer {_short(response.text)}"
    answer = response.json().get("data") if response.status_code == 200 else None
    if isinstance(answer, str) and answer.startswith(UPID_PREFIX):
        note += f"; the task ended with {_wait_for_task(target, answer)!r}"
    return note


def test_full_backup_and_restore_proxmox_lxc_round_trip(
    target: ProvisionTarget, write_log: WriteLog, scene: Scene
) -> None:
    resolver = FixedResolver(target.provider)
    configurations = JsonLinesConfigurationStore(target.store_path)
    operations = JsonLinesOperationStore(target.store_path)
    backups = JsonLinesBackupStore(target.store_path)
    backer = BackUpServer(resolver, configurations, operations, backups)
    lister = ListBackups(resolver, configurations, backups)
    restorer = RestoreServer(resolver, configurations, operations)
    tracker = tracker_of(target)
    configuration_id = target.configuration.configuration_id
    named = os.environ.get(BACKUP_STORAGE_VARIABLE, "").strip() or None
    experiment = os.environ.get(EXPERIMENT_VARIABLE, "").strip() == "1"
    product = write_log.product
    seen: list[str] = []

    # 1) Create the container through the product; it is stopped.
    vmid, _ = create_container(target, product, seen)
    scene.vmid = vmid
    assert product == ["POST"]
    server = target.provider.get_server(target.configuration, vmid)
    assert server.name == target.run_name
    assert server.state == STOPPED
    original = _description(target, vmid)

    # 2) Back it up through the product.
    preview = backer.preview_backup(configuration_id, vmid, named)
    seen.append(repr(preview))
    if preview.outcome is BackupOutcome.NO_BACKUP_STORAGE:
        pytest.fail(NO_STORAGE_MESSAGE)
    assert preview.outcome is BackupOutcome.READY, preview.reason
    assert preview.kind is BackupKind.ARCHIVE
    assert preview.storage is not None
    storage = preview.storage
    scene.storages.add(storage)
    assert product == ["POST"]

    unconfirmed = backer.request_backup(
        configuration_id, vmid, named, Confirmation(given=False)
    )
    assert unconfirmed.outcome is BackupOutcome.NOT_CONFIRMED
    assert product == ["POST"]

    requested = backer.request_backup(
        configuration_id, vmid, named, Confirmation(given=True)
    )
    seen.append(repr(requested))
    assert requested.outcome is BackupOutcome.REQUESTED, requested.reason
    assert requested.operation is not None
    assert product == ["POST", "POST"]
    ended = tracker.follow_operation(requested.operation.operation_id)
    seen.extend((repr(ended), ended.detail or ""))
    assert ended.state is OperationState.SUCCEEDED, ended.detail
    completed = backer.complete_backup(requested.operation.operation_id)
    seen.append(repr(completed))
    assert completed.outcome is BackupOutcome.COMPLETED, completed.reason
    assert completed.recorded
    assert completed.backup is not None
    volid = completed.backup.backup_id
    recorded = backups.load_by_operation(OperationId(requested.operation.operation_id))
    assert recorded is not None
    assert recorded.backup_id == volid

    listing = lister.list_backups(configuration_id, vmid)
    seen.append(repr(listing))
    assert listing.outcome is BackupOutcome.LISTED, listing.reason
    listed = [b for b in listing.backups if b.backup_id == volid]
    assert len(listed) == 1
    assert listed[0].made_by_client
    assert listed[0].alias
    assert listed[0].alias == completed.backup.alias

    # 3) Change the container so that a restore is detectable (test-only write).
    changed = f"changed-{target.run_name}"
    assert changed != original
    _change_description(target, vmid, changed)
    assert _description(target, vmid) == changed

    # 4) Restore it through the product.
    restore_preview = restorer.preview_restore(configuration_id, vmid, volid)
    seen.append(repr(restore_preview))
    assert restore_preview.outcome is RestoreOutcome.READY, restore_preview.reason
    for phrase in ("overwritten", "disks", "configuration", "cannot be undone"):
        assert phrase in restore_preview.consequence
    before = len(product)
    wrong = restorer.request_restore(
        configuration_id, vmid, volid, Confirmation(True, vmid + "0")
    )
    assert wrong.outcome is RestoreOutcome.IDENTIFIER_MISMATCH
    none = restorer.request_restore(
        configuration_id, vmid, volid, Confirmation(given=False)
    )
    assert none.outcome is RestoreOutcome.NOT_CONFIRMED
    assert len(product) == before

    restoring = restorer.request_restore(
        configuration_id, vmid, volid, Confirmation(True, vmid)
    )
    seen.append(repr(restoring))
    assert restoring.outcome is RestoreOutcome.REQUESTED, restoring.reason
    assert restoring.operation is not None
    restore_id = restoring.operation.operation_id
    ended = tracker.follow_operation(restore_id)
    seen.extend((repr(ended), ended.detail or ""))
    assert ended.state is OperationState.SUCCEEDED, ended.detail
    restored = restorer.complete_restore(restore_id)
    seen.append(repr(restored))
    assert restored.outcome is RestoreOutcome.COMPLETED, restored.reason
    assert restored.state_after == STOPPED
    assert _description(target, vmid) == original
    assert operations.load(OperationId(restore_id)).consequence == (
        restore_preview.consequence
    )

    # 5) Optional experiment: a restore over a running guest.
    if experiment:
        _experiment(target, vmid, volid, write_log, seen)
        assert target.provider.get_server(target.configuration, vmid).state == STOPPED

    # 6) Clean up through the product, then delete the archive of this run.
    seen.extend(remove_through_product(target, product, vmid))
    _delete_archive(target, storage, vmid, volid)
    assert volid not in _archives_of(target, storage, vmid)

    # 7) Exactly these writes, in this order.
    assert write_log.everything == [
        "POST",
        "POST",
        "test-only PUT",
        "POST",
        *(["POST", "test-only POST", "POST"] if experiment else []),
        "DELETE",
        "test-only DELETE",
    ]
    assert product == [
        "POST",
        "POST",
        "POST",
        *(["POST", "POST"] if experiment else []),
        "DELETE",
    ]

    seen.append(target.store_path.read_text(encoding="utf-8"))
    assert not any(target.leaks(text) for text in seen), (
        "a stored or reported text contains a credential value"
    )
