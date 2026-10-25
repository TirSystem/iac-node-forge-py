"""Backup and restore behaviours specific to the Proxmox VE adapter (mock API)."""

import inspect
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from iac_node_forge.application.errors import (
    ProviderTimeoutError,
    ProviderUnreachableError,
    ServerNotFoundError,
)
from iac_node_forge.application.ports import BackupBlock
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.operation import (
    MAX_CONSEQUENCE_LENGTH,
    Operation,
    OperationId,
    OperationState,
)
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.server import Server
from iac_node_forge.infrastructure.proxmox import ProxmoxProvider
from iac_node_forge.infrastructure.proxmox import _backup as backup_module
from tests.contract.conftest import TOKEN, WRONG_TOKEN, configuration_for
from tests.contract.mock_proxmox import MockProxmoxServer

MAX_POLLS = 20
LXC_ARCHIVE = "local:backup/vzdump-lxc-101-2026_10_20-10_00_00.tar"
OTHER_STORAGE = {"storage": "extra", "content": "backup"}


@pytest.fixture
def configuration(mock_proxmox: MockProxmoxServer) -> ProviderConfiguration:
    return configuration_for(mock_proxmox.address)


@pytest.fixture
def provider(mock_proxmox: MockProxmoxServer) -> ProxmoxProvider:
    return ProxmoxProvider(Credentials.single(TOKEN), timeout=2.0)


def server_of(
    provider: ProxmoxProvider, configuration: ProviderConfiguration, vmid: str
) -> Server:
    return provider.get_server(configuration, vmid)


def follow(
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
    operation: Operation,
) -> Operation:
    for _ in range(MAX_POLLS):
        operation = provider.operation_state(configuration, operation)
        if operation.has_ended:
            break
    return operation


def back_up(
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
    vmid: str = "101",
    storage: str | None = "local",
) -> Operation:
    return provider.request_backup(
        configuration, server_of(provider, configuration, vmid), storage
    )


def made(
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
    vmid: str = "101",
) -> Backup:
    final = follow(provider, configuration, back_up(provider, configuration, vmid))
    assert final.state is OperationState.SUCCEEDED
    found = provider.find_backup(configuration, final)
    assert found is not None
    return found


def seed(
    mock: MockProxmoxServer,
    volid: str = LXC_ARCHIVE,
    vmid: int = 101,
    ctime: int | None = 1_700_000_000,
    notes: str | None = None,
    key: str = "pve2/local",
    **extra: Any,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "volid": volid,
        "content": "backup",
        "format": "tar",
        "size": 2048,
        "vmid": vmid,
        **extra,
    }
    if ctime is not None:
        item["ctime"] = ctime
    if notes is not None:
        item["notes"] = notes
    mock.state.storage_content.setdefault(key, []).append(item)
    return item


def test_the_kind_is_archive(
    provider: ProxmoxProvider, configuration: ProviderConfiguration
) -> None:
    assert provider.backup_kind(configuration) is BackupKind.ARCHIVE


def test_the_storages_are_the_ones_of_the_guests_node_that_hold_backups(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.storages["pve2"].append(OTHER_STORAGE)
    mock_proxmox.state.storages["pve2"].append(
        {"storage": "local-lvm", "content": "rootdir,images"}
    )

    on_pve2 = provider.backup_storages(
        configuration, server_of(provider, configuration, "101")
    )
    on_pve1 = provider.backup_storages(
        configuration, server_of(provider, configuration, "100")
    )

    assert on_pve2 == ("local", "extra")
    assert on_pve1 == ("local",)
    storage_reads = [
        (r.path, r.query)
        for r in mock_proxmox.state.requests
        if r.path.endswith("/storage")
    ]
    assert ("/api2/json/nodes/pve2/storage", "content=backup") in storage_reads


def test_a_listed_storage_whose_content_lacks_backup_is_dropped() -> None:
    listing: list[Any] = [
        {"storage": "a", "content": "iso,backup"},
        {"storage": "b", "content": "iso"},
        {"storage": "c"},
        {"content": "backup"},
        "text",
    ]

    assert backup_module.storage_names(listing) == ("a", "c")


@pytest.mark.parametrize(
    ("storage", "expected"),
    [
        (None, None),
        ("local", None),
        ("local-lvm", BackupBlock.STORAGE_UNSUITABLE),
        ("ghost", BackupBlock.STORAGE_UNSUITABLE),
    ],
)
def test_the_block_depends_on_the_named_storage(
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
    storage: str | None,
    expected: BackupBlock | None,
) -> None:
    server = server_of(provider, configuration, "100")

    assert provider.backup_block(configuration, server, storage) is expected


def test_several_storages_and_none_named_is_not_a_block(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.storages["pve2"].append(OTHER_STORAGE)
    server = server_of(provider, configuration, "101")

    assert provider.backup_block(configuration, server, None) is None


def test_the_dump_posts_the_documented_fields_with_the_operation_in_the_notes(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = back_up(provider, configuration)

    assert mock_proxmox.write_classes == [("vzdump", "/api2/json/nodes/pve2/vzdump")]
    (form,) = mock_proxmox.state.dumped
    assert form["vmid"] == "101"
    assert form["storage"] == "local"
    assert form["mode"] == "snapshot"
    assert form["remove"] == "0"
    assert form["notes-template"] == (
        f"iac-node-forge {operation.operation_id} {{{{guestname}}}}"
    )
    assert operation.state is OperationState.RUNNING
    assert operation.action is PowerAction.BACKUP
    assert (operation.provider_reference or "").startswith("UPID:pve2:")


def test_the_only_storage_that_holds_backups_is_used_when_none_is_named(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = back_up(provider, configuration, storage=None)

    assert operation.state is OperationState.RUNNING
    assert mock_proxmox.state.dumped[0]["storage"] == "local"


@pytest.mark.parametrize("storage", ["local-lvm", "ghost"])
def test_a_storage_that_cannot_hold_a_backup_gives_failed_and_sends_no_write(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
    storage: str,
) -> None:
    operation = back_up(provider, configuration, storage=storage)

    assert operation.state is OperationState.FAILED
    assert BackupBlock.STORAGE_UNSUITABLE.value in (operation.detail or "")
    assert mock_proxmox.write_requests == []


def test_several_storages_and_none_named_gives_failed_and_sends_no_write(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.storages["pve2"].append(OTHER_STORAGE)

    operation = back_up(provider, configuration, storage=None)

    assert operation.state is OperationState.FAILED
    assert "none named" in (operation.detail or "")
    assert mock_proxmox.write_requests == []


def test_no_storage_that_holds_backups_gives_failed_and_sends_no_write(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.storages["pve2"] = []

    operation = back_up(provider, configuration, storage=None)

    assert operation.state is OperationState.FAILED
    assert BackupBlock.NO_BACKUP_STORAGE.value in (operation.detail or "")
    assert mock_proxmox.write_requests == []


def test_an_unknown_identifier_is_not_found_and_sends_no_write(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    ghost = Server("999", "ghost", "stopped", "", (), False)

    with pytest.raises(ServerNotFoundError):
        provider.request_backup(configuration, ghost, "local")
    with pytest.raises(ServerNotFoundError):
        provider.request_restore(
            configuration,
            ghost,
            Backup("x", BackupKind.ARCHIVE, "x", datetime.now(UTC)),
        )

    assert mock_proxmox.write_requests == []


def test_a_running_guest_can_be_backed_up_and_is_not_stopped(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    final = follow(provider, configuration, back_up(provider, configuration, "100"))

    assert final.state is OperationState.SUCCEEDED
    assert mock_proxmox.state.resources[0]["status"] == "running"
    assert [c for c, _ in mock_proxmox.write_classes] == ["vzdump"]


def test_the_archive_is_found_by_its_marker_and_described(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = back_up(provider, configuration)
    final = follow(provider, configuration, operation)

    backup = provider.find_backup(configuration, final)

    assert backup is not None
    assert backup.backup_id.startswith("local:backup/vzdump-lxc-101-")
    assert backup.kind is BackupKind.ARCHIVE
    assert backup.guest_type == "lxc"
    assert backup.size_bytes == 1048576
    assert backup.made_by_client is True
    assert backup.label == f"iac-node-forge {operation.operation_id} db"
    assert "storage local" in backup.location
    assert backup.backup_id in backup.location


def test_an_archive_that_is_not_this_operations_is_not_taken(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = back_up(provider, configuration)
    final = follow(provider, configuration, operation)
    future = 4_000_000_000
    seed(mock_proxmox, "local:backup/vzdump-lxc-101-a.tar", ctime=future, notes="mine")
    seed(
        mock_proxmox,
        "local:backup/vzdump-lxc-101-b.tar",
        ctime=future + 1,
        notes="iac-node-forge 0123456789abcdef another operation",
    )

    backup = provider.find_backup(configuration, final)

    assert backup is not None
    assert backup.label == f"iac-node-forge {operation.operation_id} db"


def test_an_archive_older_than_the_operation_is_not_taken(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = back_up(provider, configuration)
    mock_proxmox.state.dump_leaves_no_archive = True
    final = follow(provider, configuration, operation)
    seed(
        mock_proxmox,
        "local:backup/vzdump-lxc-101-old.tar",
        ctime=1_000_000_000,
        notes=f"iac-node-forge {operation.operation_id} db",
    )

    assert final.state is OperationState.SUCCEEDED
    assert provider.find_backup(configuration, final) is None


def test_the_newest_of_two_archives_with_the_marker_is_taken(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    operation = back_up(provider, configuration)
    mock_proxmox.state.dump_leaves_no_archive = True
    final = follow(provider, configuration, operation)
    notes = f"iac-node-forge {operation.operation_id} db"
    start = int(operation.started_at.timestamp())
    seed(
        mock_proxmox, "local:backup/vzdump-lxc-101-a.tar", ctime=start + 5, notes=notes
    )
    seed(
        mock_proxmox, "local:backup/vzdump-lxc-101-b.tar", ctime=start + 9, notes=notes
    )

    backup = provider.find_backup(configuration, final)

    assert backup is not None
    assert backup.backup_id == "local:backup/vzdump-lxc-101-b.tar"


def test_a_successful_dump_without_an_archive_finds_none(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.dump_leaves_no_archive = True

    final = follow(provider, configuration, back_up(provider, configuration))

    assert final.state is OperationState.SUCCEEDED
    assert provider.find_backup(configuration, final) is None


def test_an_archive_without_a_creation_time_is_neither_listed_nor_found(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.archive_without_ctime = True
    final = follow(provider, configuration, back_up(provider, configuration))
    server = server_of(provider, configuration, "101")

    assert final.state is OperationState.SUCCEEDED
    assert provider.find_backup(configuration, final) is None
    assert provider.list_backups(configuration, server) == ()


def test_an_operation_without_a_server_finds_no_backup(
    provider: ProxmoxProvider, configuration: ProviderConfiguration
) -> None:
    operation = back_up(provider, configuration)
    nameless = Operation(
        operation.operation_id,
        operation.configuration_id,
        None,
        PowerAction.BACKUP,
        OperationState.RUNNING,
        operation.started_at,
    )

    assert provider.find_backup(configuration, nameless) is None


def test_the_listing_marks_the_archives_of_this_client_and_leaves_others_out(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    seed(mock_proxmox, LXC_ARCHIVE, ctime=1_700_000_100, notes="nightly\nsecond line")
    seed(mock_proxmox, "local:backup/vzdump-lxc-101-x.tar", ctime=1_700_000_000)
    seed(mock_proxmox, "local:backup/vzdump-lxc-102-x.tar", vmid=102)
    seed(mock_proxmox, "local:iso/debian.iso", content="iso")
    seed(mock_proxmox, "local:backup/vzdump-lxc-101-n.tar", ctime=None)
    seed(mock_proxmox, "", ctime=1)
    seed(mock_proxmox, "local:backup/vzdump-qemu-101-y.vma", ctime=1_700_000_200)
    mine = made(provider, configuration)
    server = server_of(provider, configuration, "101")

    backups = provider.list_backups(configuration, server)

    assert [b.created_at for b in backups] == sorted(b.created_at for b in backups)
    by_id = {b.backup_id: b for b in backups}
    assert set(by_id) == {
        LXC_ARCHIVE,
        "local:backup/vzdump-lxc-101-x.tar",
        "local:backup/vzdump-qemu-101-y.vma",
        mine.backup_id,
    }
    assert by_id[LXC_ARCHIVE].label == "nightly"
    assert by_id[LXC_ARCHIVE].made_by_client is False
    assert by_id[LXC_ARCHIVE].size_bytes == 2048
    assert by_id[LXC_ARCHIVE].created_at == datetime.fromtimestamp(1_700_000_100, UTC)
    assert by_id[mine.backup_id].made_by_client is True
    assert by_id["local:backup/vzdump-qemu-101-y.vma"].guest_type == "qemu"


def test_the_listing_covers_every_backup_storage_of_the_node(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.storages["pve2"].append(OTHER_STORAGE)
    seed(mock_proxmox, "extra:backup/vzdump-lxc-101-z.tar", key="pve2/extra")
    seed(mock_proxmox, LXC_ARCHIVE, ctime=1_700_000_100)
    server = server_of(provider, configuration, "101")

    backups = provider.list_backups(configuration, server)

    assert {b.backup_id for b in backups} == {
        LXC_ARCHIVE,
        "extra:backup/vzdump-lxc-101-z.tar",
    }
    assert any("storage extra" in b.location for b in backups)


def test_the_content_listing_asks_for_the_guest_and_the_backup_content(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    server = server_of(provider, configuration, "101")
    mock_proxmox.state.requests.clear()

    provider.list_backups(configuration, server)

    reads = [(r.path, r.query) for r in mock_proxmox.state.requests]
    assert (
        "/api2/json/nodes/pve2/storage/local/content",
        "content=backup&vmid=101",
    ) in reads


def test_a_restore_of_a_container_posts_the_archive_as_template_with_force(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    backup = made(provider, configuration)
    mock_proxmox.state.resources[1]["name"] = "changed"

    operation = provider.request_restore(
        configuration, server_of(provider, configuration, "101"), backup
    )

    assert mock_proxmox.write_classes[-1] == (
        "restore",
        "/api2/json/nodes/pve2/lxc",
    )
    assert mock_proxmox.state.restored == [
        {
            "vmid": "101",
            "ostemplate": backup.backup_id,
            "restore": "1",
            "force": "1",
        }
    ]
    assert operation.state is OperationState.RUNNING
    assert operation.action is PowerAction.RESTORE
    assert (operation.provider_reference or "").startswith("UPID:pve2:")
    final = follow(provider, configuration, operation)
    assert final.state is OperationState.SUCCEEDED
    assert mock_proxmox.state.resources[1]["name"] == "db"
    assert mock_proxmox.state.resources[1]["status"] == "stopped"


def test_a_restore_of_a_virtual_machine_posts_the_archive_with_force(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.resources[0]["status"] = "stopped"
    backup = made(provider, configuration, "100")
    assert backup.guest_type == "qemu"
    mock_proxmox.state.resources[0]["name"] = "changed"

    operation = provider.request_restore(
        configuration, server_of(provider, configuration, "100"), backup
    )

    assert mock_proxmox.write_classes[-1] == (
        "restore",
        "/api2/json/nodes/pve1/qemu",
    )
    assert mock_proxmox.state.restored == [
        {"vmid": "100", "archive": backup.backup_id, "force": "1"}
    ]
    assert follow(provider, configuration, operation).state is (
        OperationState.SUCCEEDED
    )
    assert mock_proxmox.state.resources[0]["name"] == "web"


def test_a_restore_never_sends_start_unique_or_storage(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    backup = made(provider, configuration)

    provider.request_restore(
        configuration, server_of(provider, configuration, "101"), backup
    )

    (form,) = mock_proxmox.state.restored
    assert not {"start", "unique", "storage"} & form.keys()


def test_a_running_guest_is_blocked_and_the_restore_is_not_sent(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    backup = made(provider, configuration)
    running = Server("101", "db", "running", "", (), False)
    written = len(mock_proxmox.write_requests)

    block = provider.restore_block(configuration, running, backup)
    operation = provider.request_restore(configuration, running, backup)

    assert block is BackupBlock.RUNNING
    assert operation.state is OperationState.FAILED
    assert BackupBlock.RUNNING.value in (operation.detail or "")
    assert len(mock_proxmox.write_requests) == written
    assert mock_proxmox.state.resources[1]["status"] == "stopped"


def test_the_mock_refuses_a_restore_over_a_running_guest_as_documented(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    backup = made(provider, configuration)
    mock_proxmox.state.resources[1]["status"] = "running"
    stopped = Server("101", "db", "stopped", "", (), False)

    operation = provider.request_restore(configuration, stopped, backup)

    assert operation.state is OperationState.FAILED
    assert "500" in (operation.detail or "")
    assert mock_proxmox.state.restored == []


@pytest.mark.parametrize(
    "volume",
    [
        "local:backup/vzdump-qemu-101-2026_10_20-10_00_00.vma",
        "local:backup/vzdump-lxc-105-2026_10_20-10_00_00.tar",
    ],
)
def test_an_archive_of_another_type_or_vmid_is_a_mismatch_and_not_sent(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
    volume: str,
) -> None:
    stranger = Backup(volume, BackupKind.ARCHIVE, volume, datetime.now(UTC))
    server = server_of(provider, configuration, "101")

    block = provider.restore_block(configuration, server, stranger)
    operation = provider.request_restore(configuration, server, stranger)

    assert block is BackupBlock.MISMATCH
    assert operation.state is OperationState.FAILED
    assert mock_proxmox.write_requests == []


def test_a_guest_type_given_by_the_backup_must_match_the_guest(
    provider: ProxmoxProvider, configuration: ProviderConfiguration
) -> None:
    server = server_of(provider, configuration, "101")
    odd = Backup(
        "local:backup/custom-name.tar",
        BackupKind.ARCHIVE,
        "x",
        datetime.now(UTC),
        guest_type="qemu",
    )

    assert provider.restore_block(configuration, server, odd) is BackupBlock.MISMATCH


def test_the_effect_names_the_guest_the_archive_and_that_nothing_can_be_undone(
    provider: ProxmoxProvider, configuration: ProviderConfiguration
) -> None:
    backup = Backup(
        LXC_ARCHIVE,
        BackupKind.ARCHIVE,
        "x",
        datetime(2026, 10, 20, 10, 0, 5, tzinfo=UTC),
    )
    server = server_of(provider, configuration, "101")

    effect = provider.restore_effect(configuration, server, backup)

    text = effect.consequence
    assert "Guest 101 (db) on node pve2 is overwritten by the backup" in text
    assert LXC_ARCHIVE in text
    assert "2026-10-20 10:00:05 UTC" in text
    assert "disks and its configuration are replaced" in text
    assert "not documented" in text
    assert "cannot be undone" in text
    assert effect.deleted_backups == ()


def test_a_long_name_and_volume_still_fit_the_consequence_bound(
    provider: ProxmoxProvider, configuration: ProviderConfiguration
) -> None:
    backup = Backup(
        "local:backup/vzdump-lxc-101-" + "x" * 300 + ".tar",
        BackupKind.ARCHIVE,
        "x",
        datetime.now(UTC),
    )
    server = Server("101", "n" * 300, "stopped", "", (), False)

    text = provider.restore_effect(configuration, server, backup).consequence
    dump = provider.backup_consequence(configuration, server, "local")

    assert len(text) <= MAX_CONSEQUENCE_LENGTH
    assert "cannot be undone" in text
    assert len(dump) <= MAX_CONSEQUENCE_LENGTH


def test_the_backup_consequence_says_where_the_archive_goes_and_that_nothing_is_deleted(
    provider: ProxmoxProvider, configuration: ProviderConfiguration
) -> None:
    server = server_of(provider, configuration, "101")

    text = provider.backup_consequence(configuration, server, "local")

    assert "guest 101 (db) on node pve2 to storage local" in text
    assert "deletes no earlier backup" in text
    assert "not off-site" in text


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_dump_names_the_permissions_it_needs(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
    status: int,
) -> None:
    mock_proxmox.state.dump_forced_status = status

    operation = back_up(provider, configuration)

    assert operation.state is OperationState.FAILED
    detail = operation.detail or ""
    assert "VM.Backup" in detail
    assert "Datastore.AllocateSpace" in detail
    assert str(status) in detail
    assert TOKEN not in detail


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_restore_names_the_permissions_it_needs(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
    status: int,
) -> None:
    backup = made(provider, configuration)
    mock_proxmox.state.restore_forced_status = status

    operation = provider.request_restore(
        configuration, server_of(provider, configuration, "101"), backup
    )

    assert operation.state is OperationState.FAILED
    detail = operation.detail or ""
    assert "VM.Backup" in detail
    assert "VM.Allocate" in detail
    assert "Datastore.AllocateSpace" in detail
    assert TOKEN not in detail


def test_a_wrong_token_is_rejected_without_leaking_either_token(
    mock_proxmox: MockProxmoxServer, configuration: ProviderConfiguration
) -> None:
    right = ProxmoxProvider(Credentials.single(TOKEN), timeout=2.0)
    wrong = ProxmoxProvider(Credentials.single(WRONG_TOKEN), timeout=2.0)
    server = server_of(right, configuration, "101")

    operation = wrong.request_backup(configuration, server, "local")

    assert operation.state is OperationState.FAILED
    assert operation.detail is not None
    assert TOKEN not in operation.detail
    assert WRONG_TOKEN not in operation.detail
    assert mock_proxmox.state.dumped == []


def test_a_server_error_on_the_dump_is_a_plain_failure(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.dump_forced_status = 500

    operation = back_up(provider, configuration)

    assert operation.state is OperationState.FAILED
    assert "500" in (operation.detail or "")


def test_a_timeout_on_the_dump_is_unknown_and_keeps_the_operation_identifier(
    monkeypatch: pytest.MonkeyPatch,
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    def time_out(*args: object, **kwargs: object) -> object:
        raise ProviderTimeoutError("the provider did not answer within 2.0 seconds")

    monkeypatch.setattr(provider, "_post", time_out)

    operation = back_up(provider, configuration)

    assert operation.state is OperationState.UNKNOWN
    assert operation.server_identifier == "101"
    assert operation.action is PowerAction.BACKUP
    assert "may have reached the provider" in (operation.detail or "")
    assert mock_proxmox.write_requests == []


def test_a_refused_connection_on_the_restore_is_a_plain_failure(
    monkeypatch: pytest.MonkeyPatch,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    backup = made(provider, configuration)

    def refuse(*args: object, **kwargs: object) -> object:
        raise ProviderUnreachableError("the provider could not be reached")

    monkeypatch.setattr(provider, "_post", refuse)

    operation = provider.request_restore(
        configuration, server_of(provider, configuration, "101"), backup
    )

    assert operation.state is OperationState.FAILED


def test_a_task_that_ends_with_an_error_status_leaves_no_archive(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    mock_proxmox.state.task_exitstatus = "job errors"

    final = follow(provider, configuration, back_up(provider, configuration))

    assert final.state is OperationState.FAILED
    assert final.detail == "job errors"
    kept = mock_proxmox.state.storage_content["pve2/local"]
    assert [item for item in kept if item["content"] == "backup"] == []
    assert provider.find_backup(configuration, final) is None


def test_the_mock_refuses_a_restore_onto_an_existing_guest_without_force(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    backup = made(provider, configuration)

    response = httpx.post(
        mock_proxmox.address + "/api2/json/nodes/pve2/lxc",
        headers={"Authorization": f"PVEAPIToken={TOKEN}"},
        data={"vmid": "101", "ostemplate": backup.backup_id, "restore": "1"},
    )

    assert response.status_code == 500
    assert mock_proxmox.state.restored == []


def test_a_reread_state_after_the_restore_is_the_one_the_archive_held(
    mock_proxmox: MockProxmoxServer,
    provider: ProxmoxProvider,
    configuration: ProviderConfiguration,
) -> None:
    backup = made(provider, configuration)
    mock_proxmox.state.resources[1].update(name="changed", maxcpu=9)
    follow(
        provider,
        configuration,
        provider.request_restore(
            configuration, server_of(provider, configuration, "101"), backup
        ),
    )

    restored = server_of(provider, configuration, "101")

    assert restored.name == "db"
    assert restored.size == "4 vCPU, 8192 MiB"


def test_the_reads_send_no_write_and_the_backup_module_names_no_http_call() -> None:
    source = inspect.getsource(backup_module)

    for call in ("httpx", ".post(", ".put(", ".delete(", ".patch(", ".request("):
        assert call not in source
    assert source.count('"remove": "0"') == 1
    assert source.count('"mode": "snapshot"') == 1
    for other in ("rollback", "resize", "clone", "migrate", "download"):
        assert other not in source


def test_the_volume_name_encodes_the_guest_type_and_vmid() -> None:
    assert backup_module.guest_of_volume(LXC_ARCHIVE) == ("lxc", "101")
    assert backup_module.guest_of_volume("s:backup/vzdump-qemu-7-x.vma") == (
        "qemu",
        "7",
    )
    assert backup_module.guest_of_volume("s:backup/other.tar") is None
    assert backup_module.guest_of_volume("s:iso/vzdump-lxc-1-x.iso") is None


def test_the_marker_is_recognised_only_at_the_start_of_the_notes() -> None:
    assert backup_module.has_marker("iac-node-forge abc db")
    assert not backup_module.has_marker("note iac-node-forge abc")
    assert not backup_module.has_marker("iac-node-forgery")
    assert not backup_module.has_marker(None)
    assert backup_module.carries_operation("iac-node-forge abc db", OperationId("abc"))
    assert not backup_module.carries_operation(
        "iac-node-forge abd db", OperationId("abc")
    )
