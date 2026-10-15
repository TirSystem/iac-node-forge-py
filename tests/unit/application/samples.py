"""Sample domain data shared by the use case and command-line tests."""

from datetime import UTC, datetime

from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.server import Server

SECRET = "root@pam!forge=00000000-1111-2222-3333-444444444444"
LAB_ID = ConfigurationId("cfg-lab")
EDGE_ID = ConfigurationId("cfg-edge")

LAB = ProviderConfiguration(
    LAB_ID, ProviderKind.PROXMOX_VE, "Lab", "https://lab.example:8006"
)
EDGE = ProviderConfiguration(
    EDGE_ID, ProviderKind.PROXMOX_VE, "Edge", "https://edge.example:8006"
)

WEB = Server("100", "web", "running", "2 vCPU, 2048 MiB", ("10.0.0.5",), False)
DB = Server("101", "db", "stopped", "4 vCPU, 8192 MiB", (), False)
CACHE = Server("200", "cache", "running", "1 vCPU, 512 MiB", ("10.0.1.7",), False)

WEB_IS_MANAGED = ManagedServerRecord(LAB_ID, "100", datetime(2026, 1, 1, tzinfo=UTC))
