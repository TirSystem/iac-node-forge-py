"""One ProviderPort implementation with the data it serves, for contract tests."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from iac_node_forge.application.ports import (
    BackupBlock,
    ProviderPort,
    RemovalBlock,
    ServerBackupPort,
    ServerPowerPort,
    ServerProvisionPort,
    ServerRemovalPort,
)
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.removal_kind import RemovalKind
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_option import ServerOption


@dataclass(frozen=True, slots=True)
class ProviderUnderTest:
    """A provider, a configuration that reaches it, and what it must answer.

    Every variant (fake, Proxmox VE, Contabo) fills the same fields from its own
    backing data, so one set of behaviour tests runs against all of them.
    """

    name: str
    provider: ProviderPort
    configuration: ProviderConfiguration
    credentials: Credentials
    wrong_credentials: Credentials
    expected_servers: tuple[Server, ...]
    expected_options: Mapping[str, tuple[ServerOption, ...]]
    secret_values: tuple[str, ...]
    # The same adapter object as ``provider``, seen through the power port.
    power: ServerPowerPort
    # Makes every later power request be rejected by the provider (403).
    reject_power_requests: Callable[[], None]
    # Makes every later operation end in failure at the provider.
    fail_operations: Callable[[], None]
    # The write requests the variant's backend received, as (method, target).
    write_requests: Callable[[], list[tuple[str, str]]]
    # The write requests the allow-list permits for servers 100 and 101.
    allowed_write_requests: frozenset[tuple[str, str]]
    missing_identifier: str = "999"
    unknown_option_kind: str = "flavour"


@dataclass(frozen=True, slots=True)
class ProvisionCase:
    """A provider seen through the provision port, with a description to create.

    Every variant (fake, Proxmox VE, Contabo) fills the same fields, so one set
    of provisioning tests runs against all of them.
    """

    name: str
    provider: ProviderPort
    provision: ServerProvisionPort
    configuration: ProviderConfiguration
    desired: DesiredConfiguration
    server_name: str
    # The value names every creation needs; value_rules must declare them.
    required_value_names: frozenset[str]
    secret_values: tuple[str, ...]
    # Makes every later creation be rejected by the provider (403).
    reject_creation: Callable[[], None]
    # What a rejection tells the user about the missing permission, or None.
    rejection_hint: str | None
    # Makes every later creation end in failure at the provider.
    fail_operations: Callable[[], None]
    # The write requests the variant's backend received, as (method, target).
    write_requests: Callable[[], list[tuple[str, str]]]
    # The creation requests the allow-list permits.
    allowed_write_requests: frozenset[tuple[str, str]]
    # Whether the provider chooses the identifier before the request (Proxmox VE).
    reserves_identifier: bool
    # Whether the provider imposes a commitment it must state before creation.
    states_cost: bool


@dataclass(frozen=True, slots=True)
class RemovalCase:
    """A provider seen through the removal port, with servers to remove.

    Every variant (fake, Proxmox VE, Contabo) fills the same fields, so one set
    of removal tests runs against all of them. Server ``101`` is stopped and can
    be removed; ``blocked_id`` is a server the provider blocks once
    ``prepare_blocked`` has run.
    """

    name: str
    provider: ProviderPort
    removal: ServerRemovalPort
    configuration: ProviderConfiguration
    secret_values: tuple[str, ...]
    kind: RemovalKind
    removable_id: str
    blocked_id: str
    expected_block: RemovalBlock
    # Brings the blocked server into the state the provider blocks.
    prepare_blocked: Callable[[], None]
    # Makes every later removal be rejected by the provider (403).
    reject_removal: Callable[[], None]
    # What a rejection tells the user about the missing permission, or None.
    rejection_hint: str | None
    # Makes every later removal end in failure at the provider.
    fail_operations: Callable[[], None]
    # Makes the answer to every later removal request get lost (a timeout).
    lose_answers: Callable[[], None]
    # The write requests the variant's backend received, as (method, target).
    write_requests: Callable[[], list[tuple[str, str]]]
    # The removal requests the allow-list permits.
    allowed_write_requests: frozenset[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class BackupCase:
    """A provider seen through the backup port, with servers to back up.

    Every variant (fake, Proxmox VE, Contabo) fills the same fields, so one set
    of backup and restore tests runs against all of them. Server ``101`` is
    stopped and can be backed up and restored; ``blocked_id`` is a server whose
    restore is blocked once ``prepare_blocked`` has run.
    """

    name: str
    provider: ProviderPort
    backup: ServerBackupPort
    configuration: ProviderConfiguration
    secret_values: tuple[str, ...]
    kind: BackupKind
    server_id: str
    blocked_id: str
    # A storage the user may name, or None where the provider has no storage.
    storage: str | None
    expected_storages: tuple[str, ...]
    # A storage that must be refused when named.
    unsuitable_storage: str
    # Takes every backup storage away; the block a backup then reports.
    remove_storages: Callable[[], None]
    expected_no_storage_block: BackupBlock | None
    # Brings the blocked server into the state the restore is blocked in.
    prepare_blocked: Callable[[], None]
    expected_restore_block: BackupBlock
    # A backup that is not one of the servers' backups.
    foreign_backup: Backup
    # Makes every later backup or restore be rejected by the provider (403).
    reject_backup: Callable[[], None]
    reject_restore: Callable[[], None]
    # What a rejection tells the user about the missing permission, or None.
    rejection_hint: str | None
    # Makes every later operation end in failure at the provider.
    fail_operations: Callable[[], None]
    # Makes the answer to every later request get lost (a timeout).
    lose_answers: Callable[[], None]
    # Changes server 101 after its backup, and says whether a restore undid it.
    change_server: Callable[[], None]
    state_is_restored: Callable[[], bool]
    # The write requests the variant's backend received, as (class, target).
    write_requests: Callable[[], list[tuple[str, str]]]
    # The write requests the allow-list permits for the backup and the restore.
    allowed_write_requests: frozenset[tuple[str, str]]
