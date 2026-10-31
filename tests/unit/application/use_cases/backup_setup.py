"""A configured Lab with the backup fake and the three backup use cases."""

from dataclasses import dataclass
from datetime import timedelta

from iac_node_forge.application.dto import Confirmation
from iac_node_forge.application.use_cases import (
    BackUpServer,
    ListBackups,
    RestoreServer,
    TrackOperation,
)
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.operation import Operation, OperationId
from iac_node_forge.domain.server import Server
from tests.unit.application.fakes import (
    FakeBackupProvider,
    FakeBackupStore,
    FakeClock,
    FakeConfigurationStore,
    FakeOperationStore,
    FakeProviderResolver,
)
from tests.unit.application.samples import DB, LAB, LAB_ID, WEB

CANCELLED = Server("300", "old", "cancelled", "1 vCPU", (), False)
SERVERS = (WEB, DB, CANCELLED)
MANAGED_DB = ManagedServerRecord(LAB_ID, "101", FakeClock().now)


@dataclass(slots=True)
class Setup:
    fake: FakeBackupProvider
    configurations: FakeConfigurationStore
    operations: FakeOperationStore
    backups: FakeBackupStore
    clock: FakeClock
    back_up: BackUpServer
    listing: ListBackups
    restore: RestoreServer
    tracker: TrackOperation

    def finish(self, operation_id: str) -> Operation:
        """Follow the operation to its end, as the command line does."""
        self.tracker.follow_operation(operation_id, 30, 1)
        return self.operations.load(OperationId(operation_id))

    def made_backup(self, server: str = "101", storage: str | None = None) -> Backup:
        """Back a server up and complete it; return the backup the provider lists."""
        result = self.back_up.request_backup(
            "Lab", server, storage, Confirmation(given=True)
        )
        assert result.operation is not None
        self.finish(result.operation.operation_id)
        self.back_up.complete_backup(result.operation.operation_id)
        return self.fake.list_backups(LAB, _server(server))[-1]

    def add_foreign(self, server: str, backup_id: str, *, hours: int) -> Backup:
        """Make the provider hold a backup this client did not make."""
        backup = Backup(
            backup_id=backup_id,
            kind=self.fake.backup_kind(LAB),
            location=f"elsewhere {backup_id}",
            created_at=self.clock.now + timedelta(hours=hours),
        )
        self.fake.add_backup(server, backup)
        return backup


def _server(identifier: str) -> Server:
    return next(s for s in SERVERS if s.server_identifier == identifier)


def build(
    kind: BackupKind = BackupKind.ARCHIVE,
    storages: tuple[str, ...] = ("local",),
    managed: tuple[ManagedServerRecord, ...] = (),
    supported: bool = True,
    servers: tuple[Server, ...] = SERVERS,
    provider_class: type[FakeBackupProvider] = FakeBackupProvider,
) -> Setup:
    clock = FakeClock()
    fake = provider_class(
        servers=servers,
        kind=kind,
        storages=storages,
        supported=supported,
        clock=clock,
    )
    configurations = FakeConfigurationStore(managed)
    configurations.save(LAB)
    operations = FakeOperationStore()
    backups = FakeBackupStore()
    resolver = FakeProviderResolver(fake)
    return Setup(
        fake,
        configurations,
        operations,
        backups,
        clock,
        BackUpServer(resolver, configurations, operations, backups),
        ListBackups(resolver, configurations, backups),
        RestoreServer(resolver, configurations, operations),
        TrackOperation(resolver, configurations, operations, clock, clock.sleep),
    )
