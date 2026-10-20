"""Builds the transfer use cases over the in-process fakes."""

from dataclasses import dataclass, field

from iac_node_forge.application.use_cases import (
    BackUpFiles,
    DefineServerAccess,
    RemoteAccessResolver,
    RestoreFiles,
)
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_access import ServerAccess
from tests.unit.application.fakes import (
    FakeClock,
    FakeConfigurationStore,
    FakeProvider,
    FakeProviderResolver,
)
from tests.unit.application.samples import LAB, LAB_ID
from tests.unit.application.transfer_fakes import (
    SERVER_KEY,
    FakeLocalFilesystem,
    FakeRemoteHost,
    FakeServerAccessStore,
)

HOST = SERVER_KEY.host
WEB = Server("100", "web", "running", "2 vCPU", (f"{HOST}/24",), False)
DB = Server("101", "db", "stopped", "4 vCPU", (), False)
MANAGED_WEB = ManagedServerRecord(LAB_ID, "100", FakeClock().now)
DEPLOY = ServerAccess(LAB_ID, "100", HOST, 22, "deploy", None)


@dataclass
class TransferSetup:
    """The fakes behind the use cases of one test."""

    host: FakeRemoteHost
    filesystem: FakeLocalFilesystem
    accesses: FakeServerAccessStore
    configurations: FakeConfigurationStore
    servers: tuple[Server, ...] = (WEB, DB)
    clock: FakeClock = field(default_factory=FakeClock)

    def __post_init__(self) -> None:
        self.providers = FakeProviderResolver(FakeProvider(servers=self.servers))
        self.resolver = RemoteAccessResolver(
            self.providers,
            self.configurations,
            self.accesses,
            self.host,
            self.host,
        )
        self.backup = BackUpFiles(
            self.resolver, self.host, self.host, self.filesystem, self.clock
        )
        self.restore = RestoreFiles(
            self.resolver, self.host, self.host, self.filesystem, self.clock
        )
        self.define = DefineServerAccess(
            self.providers, self.configurations, self.accesses
        )


def build(
    *,
    managed: bool = True,
    access: ServerAccess | None = DEPLOY,
    free: int | None = None,
    host: FakeRemoteHost | None = None,
    servers: tuple[Server, ...] = (WEB, DB),
) -> TransferSetup:
    """Lab with a running server 100 and a stopped 101 and a seeded remote tree."""
    fake = host or FakeRemoteHost()
    if host is None:
        fake.add_file("/srv/app/a.txt", b"alpha")
        fake.add_file("/srv/app/sub/b.txt", b"beta")
    configurations = FakeConfigurationStore((MANAGED_WEB,) if managed else ())
    configurations.save(LAB)
    accesses = FakeServerAccessStore()
    if access is not None:
        accesses.save(access)
    return TransferSetup(
        fake, FakeLocalFilesystem(free), accesses, configurations, servers
    )
