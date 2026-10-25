"""The backup fake against its port, and the resolver's backup_for."""

from datetime import UTC, datetime

import pytest

from iac_node_forge.application import capabilities
from iac_node_forge.application.errors import (
    BackupUnsupportedError,
    ProviderAuthenticationError,
    UnsupportedProviderError,
)
from iac_node_forge.application.ports import (
    BackupBlock,
    ProviderResolver,
    ServerBackupPort,
)
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.operation import Operation, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.server import Server
from tests.unit.application.fakes import (
    FakeBackupProvider,
    FakeProvider,
    FakeProviderResolver,
)

CONFIGURATION = ProviderConfiguration(
    ConfigurationId("cfg-1"), ProviderKind.PROXMOX_VE, "Lab", "pve.example:8006"
)
RUNNING = Server("100", "web", "running", "small", (), False)
STOPPED = Server("101", "db", "stopped", "small", (), False)
CANCELLED = Server("102", "old", "cancelled", "small", (), False)
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def follow(fake: FakeBackupProvider, operation: Operation) -> Operation:
    for _ in range(10):
        operation = fake.operation_state(CONFIGURATION, operation)
        if operation.has_ended:
            break
    return operation


def made(fake: FakeBackupProvider, server: Server = STOPPED) -> Backup:
    final = follow(fake, fake.request_backup(CONFIGURATION, server, None))
    found = fake.find_backup(CONFIGURATION, final)
    assert found is not None
    return found


def test_fake_satisfies_the_backup_port() -> None:
    port: ServerBackupPort = FakeBackupProvider()

    assert isinstance(port, ServerBackupPort)


def test_fake_declares_the_two_capabilities_only_when_supported() -> None:
    supported = {
        c.name for c in FakeBackupProvider().discover_capabilities(CONFIGURATION)
    }
    unsupported = {
        c.name
        for c in FakeBackupProvider(supported=False).discover_capabilities(
            CONFIGURATION
        )
    }

    assert {capabilities.BACKUP_SERVER, capabilities.RESTORE_SERVER} <= supported
    assert not {capabilities.BACKUP_SERVER, capabilities.RESTORE_SERVER} & unsupported


def test_requests_without_the_capability_are_refused_and_record_nothing() -> None:
    fake = FakeBackupProvider(servers=(STOPPED,), supported=False)
    backup = Backup("b", BackupKind.ARCHIVE, "x", NOW)

    with pytest.raises(BackupUnsupportedError):
        fake.request_backup(CONFIGURATION, STOPPED, None)
    with pytest.raises(BackupUnsupportedError):
        fake.request_restore(CONFIGURATION, STOPPED, backup)

    assert fake.requests == []


def test_an_archive_blocks_a_restore_over_a_running_server_only() -> None:
    fake = FakeBackupProvider(servers=(RUNNING, STOPPED))
    backup = Backup("b1", BackupKind.ARCHIVE, "x", NOW)
    fake.add_backup("100", backup)
    fake.add_backup("101", backup)

    assert fake.restore_block(CONFIGURATION, RUNNING, backup) is BackupBlock.RUNNING
    assert fake.restore_block(CONFIGURATION, STOPPED, backup) is None


def test_a_backup_held_for_another_server_is_a_mismatch() -> None:
    fake = FakeBackupProvider(servers=(RUNNING, STOPPED))
    backup = Backup("b1", BackupKind.ARCHIVE, "x", NOW)
    fake.add_backup("100", backup)

    assert fake.restore_block(CONFIGURATION, STOPPED, backup) is BackupBlock.MISMATCH


def test_a_snapshot_blocks_a_cancelled_server_and_allows_a_running_one() -> None:
    fake = FakeBackupProvider(kind=BackupKind.SNAPSHOT)
    backup = Backup("snap-1", BackupKind.SNAPSHOT, "x", NOW, label="snap")
    fake.add_backup("100", backup)
    fake.add_backup("102", backup)

    assert fake.restore_block(CONFIGURATION, RUNNING, backup) is None
    assert fake.restore_block(CONFIGURATION, CANCELLED, backup) is BackupBlock.CANCELLED
    assert fake.backup_block(CONFIGURATION, CANCELLED, None) is BackupBlock.CANCELLED
    assert fake.backup_block(CONFIGURATION, RUNNING, None) is None


@pytest.mark.parametrize(
    ("storages", "storage", "expected"),
    [
        (("local",), None, None),
        (("local",), "local", None),
        (("local",), "other", BackupBlock.STORAGE_UNSUITABLE),
        ((), None, BackupBlock.NO_BACKUP_STORAGE),
        ((), "local", BackupBlock.STORAGE_UNSUITABLE),
    ],
)
def test_an_archive_checks_the_storage(
    storages: tuple[str, ...], storage: str | None, expected: BackupBlock | None
) -> None:
    fake = FakeBackupProvider(storages=storages)

    assert fake.backup_block(CONFIGURATION, STOPPED, storage) is expected
    assert fake.backup_storages(CONFIGURATION, STOPPED) == storages


def test_a_snapshot_has_no_storage_and_refuses_a_named_one() -> None:
    fake = FakeBackupProvider(kind=BackupKind.SNAPSHOT, storages=("local",))

    assert fake.backup_storages(CONFIGURATION, STOPPED) == ()
    assert (
        fake.backup_block(CONFIGURATION, STOPPED, "local")
        is BackupBlock.STORAGE_UNSUITABLE
    )


def test_a_backup_is_a_running_operation_that_ends_after_its_steps() -> None:
    fake = FakeBackupProvider(servers=(STOPPED,), steps_to_finish=3)

    operation = fake.request_backup(CONFIGURATION, STOPPED, "local")
    first = fake.operation_state(CONFIGURATION, operation)
    second = fake.operation_state(CONFIGURATION, first)
    third = fake.operation_state(CONFIGURATION, second)

    assert operation.state is OperationState.RUNNING
    assert operation.action is PowerAction.BACKUP
    assert (first.state, second.state) == (OperationState.RUNNING,) * 2
    assert third.state is OperationState.SUCCEEDED
    assert fake.requests == [("backup", "101")]
    assert fake.operation_state(CONFIGURATION, third) == third


def test_the_backup_is_listed_and_found_once_the_operation_succeeded() -> None:
    fake = FakeBackupProvider(servers=(STOPPED,))
    operation = fake.request_backup(CONFIGURATION, STOPPED, "local")

    assert fake.list_backups(CONFIGURATION, STOPPED) == ()
    assert fake.find_backup(CONFIGURATION, operation) is None
    final = follow(fake, operation)
    found = fake.find_backup(CONFIGURATION, final)

    assert found is not None
    assert found.made_by_client is True
    assert "storage local" in found.location
    assert fake.list_backups(CONFIGURATION, STOPPED) == (found,)


def test_a_backup_that_cannot_be_located_is_not_found() -> None:
    fake = FakeBackupProvider(servers=(STOPPED,))
    fake.hide_backups = True

    final = follow(fake, fake.request_backup(CONFIGURATION, STOPPED, None))

    assert final.state is OperationState.SUCCEEDED
    assert fake.find_backup(CONFIGURATION, final) is None


def test_a_restore_clears_the_change_and_a_snapshot_restore_deletes_newer_ones() -> (
    None
):
    fake = FakeBackupProvider(servers=(STOPPED,), kind=BackupKind.SNAPSHOT)
    first, second = made(fake), made(fake)
    fake.change_server("101")

    effect = fake.restore_effect(CONFIGURATION, STOPPED, first)
    final = follow(fake, fake.request_restore(CONFIGURATION, STOPPED, first))

    assert effect.deleted_backups == (second,)
    assert "cannot be undone" in effect.consequence
    assert final.state is OperationState.SUCCEEDED
    assert final.action is PowerAction.RESTORE
    assert fake.changed == set()
    assert fake.list_backups(CONFIGURATION, STOPPED) == (first,)


def test_an_archive_restore_deletes_nothing() -> None:
    fake = FakeBackupProvider(servers=(STOPPED,))
    first, second = made(fake), made(fake)

    effect = fake.restore_effect(CONFIGURATION, STOPPED, first)
    follow(fake, fake.request_restore(CONFIGURATION, STOPPED, first))

    assert effect.deleted_backups == ()
    assert fake.list_backups(CONFIGURATION, STOPPED) == (first, second)


def test_a_rejection_gives_failed_and_records_nothing() -> None:
    fake = FakeBackupProvider(servers=(STOPPED,))
    fake.reject_with(ProviderAuthenticationError("the fake provider answered 403"))

    operation = fake.request_backup(CONFIGURATION, STOPPED, None)

    assert operation.state is OperationState.FAILED
    assert "403" in (operation.detail or "")
    assert fake.requests == []


def test_a_lost_answer_gives_unknown_and_records_one_request() -> None:
    fake = FakeBackupProvider(servers=(STOPPED,))
    fake.answer_unknown()

    operation = fake.request_backup(CONFIGURATION, STOPPED, None)

    assert operation.state is OperationState.UNKNOWN
    assert "may have reached the provider" in (operation.detail or "")
    assert fake.requests == [("backup", "101")]


def test_operations_can_be_made_to_fail() -> None:
    fake = FakeBackupProvider(servers=(STOPPED,))
    fake.make_operations_fail()

    final = follow(fake, fake.request_backup(CONFIGURATION, STOPPED, None))

    assert final.state is OperationState.FAILED
    assert fake.list_backups(CONFIGURATION, STOPPED) == ()


def test_the_reads_are_recorded_and_send_no_request() -> None:
    fake = FakeBackupProvider(servers=(STOPPED,))

    fake.backup_kind(CONFIGURATION)
    fake.backup_storages(CONFIGURATION, STOPPED)
    fake.backup_block(CONFIGURATION, STOPPED, None)
    text = fake.backup_consequence(CONFIGURATION, STOPPED, None)
    fake.list_backups(CONFIGURATION, STOPPED)

    assert "101" in text
    assert fake.requests == []
    assert fake.reads == [
        "backup_kind",
        "backup_storages",
        "backup_block",
        "backup_consequence",
        "list_backups",
    ]


def test_resolver_gives_the_backup_port_of_a_provider_that_has_one() -> None:
    fake = FakeBackupProvider()
    resolver: ProviderResolver = FakeProviderResolver(fake)

    assert resolver.backup_for(CONFIGURATION) is fake


def test_resolver_gives_the_configured_backup_port_first() -> None:
    fake = FakeBackupProvider()
    other = FakeBackupProvider()
    resolver = FakeProviderResolver(
        FakeProvider(),
        backup=other,
        backup_by_configuration={ConfigurationId("cfg-2"): fake},
    )
    second = ProviderConfiguration(
        ConfigurationId("cfg-2"), ProviderKind.CONTABO, "Other", "api.example"
    )

    assert resolver.backup_for(CONFIGURATION) is other
    assert resolver.backup_for(second) is fake
    assert resolver.backup_resolved == [
        CONFIGURATION.configuration_id,
        second.configuration_id,
    ]


def test_resolver_refuses_a_provider_without_a_backup_port() -> None:
    resolver = FakeProviderResolver(FakeProvider())

    with pytest.raises(UnsupportedProviderError):
        resolver.backup_for(CONFIGURATION)
