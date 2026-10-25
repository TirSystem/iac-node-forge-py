"""The five ports (read, power, provision, removal, backup) for Contabo.

The four credential values are exchanged for a bearer token with the OAuth2
password grant. The token is kept only in memory, reused, and replaced once
after a 401. Every API call carries a fresh ``x-request-id``. Every API call
is a GET except the four power POSTs of ``_POWER_ACTION_PATH``
(``/actions/start``, ``shutdown``, ``stop`` and ``restart`` of one instance)
the instance creation (``POST /v1/compute/instances``, ADR-0007) and the
cancellation (``POST /v1/compute/instances/{id}/cancel``, ADR-0008), the
snapshot creation (``POST .../{id}/snapshots``) and the snapshot rollback
(``POST .../{id}/snapshots/{snapshotId}/rollback``, ADR-0010); the only other
request is the token POST (ADR-0006). Credentials: ADR-0005.
"""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import timedelta
from types import MappingProxyType
from typing import Any

import httpx

from iac_node_forge.application import capabilities
from iac_node_forge.application.errors import (
    PowerActionUnsupportedError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderPageLimitError,
    ProviderRateLimitError,
    ProviderServiceError,
    ProviderTimeoutError,
    ServerNotFoundError,
    UnknownOptionKindError,
)
from iac_node_forge.application.ports.server_backup_port import BackupBlock
from iac_node_forge.application.ports.server_removal_port import RemovalBlock
from iac_node_forge.domain.access_result import AccessResult
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.errors import MissingCredentialError
from iac_node_forge.domain.operation import Operation, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.removal_kind import RemovalKind
from iac_node_forge.domain.restore_effect import RestoreEffect
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_option import ServerOption
from iac_node_forge.domain.value_rule import ValueRule
from iac_node_forge.infrastructure import _http, _power
from iac_node_forge.infrastructure.contabo import _backup, _provision, _removal

DEFAULT_AUTH_URL = (
    "https://auth.contabo.com/auth/realms/contabo/protocol/openid-connect/token"
)
DEFAULT_TIMEOUT_SECONDS = 10.0
OPTION_KIND_REGION = "region"
OPTION_KIND_IMAGE = "image"
OPTION_KIND_PRODUCT = "product"
PAGE_SIZE = 100
MAX_PAGES = 100
INSTANCES_PATH = "/v1/compute/instances"
DATA_CENTERS_PATH = "/v1/data-centers"
IMAGES_PATH = "/v1/compute/images"
# UNVERIFIED: provider-api-verification.md (C6) names ``productId`` as an
# input of the creation but gives no path that lists the products; this
# path is a guess that only the mock answers. Confirm it against the
# Contabo API description or a live account before relying on it.
PRODUCTS_PATH = "/v1/compute/products"
DEFAULT_RESTART_SETTLE_SECONDS = 30.0
_UNKNOWN = "unknown"
_STATUS_RUNNING = "running"
_STATUS_STOPPED = "stopped"
_STATUS_ERROR = "error"
_NO_INSTANCE_NAMED = (
    "Contabo accepted the request but named no instance; check the account "
    "before trying again"
)
_ACCEPTED_WRITE_STATUSES = frozenset({200, 201, 202})
_RESTART_NOT_OBSERVED = (
    "the instance is running after the settle time; the restart itself could "
    "not be observed because Contabo reports no task"
)

# The only write requests this adapter can send, one path per power action.
_POWER_ACTION_PATH: Mapping[PowerAction, str] = MappingProxyType(
    {
        PowerAction.START: "start",
        PowerAction.SHUTDOWN: "shutdown",
        PowerAction.STOP: "stop",
        PowerAction.RESTART: "restart",
    }
)
# The status an instance has when the action is done; RESTART has none that
# differs from the starting status, so it is judged after the settle time.
_TARGET_STATUS: Mapping[PowerAction, str] = MappingProxyType(
    {
        PowerAction.START: _STATUS_RUNNING,
        PowerAction.SHUTDOWN: _STATUS_STOPPED,
        PowerAction.STOP: _STATUS_STOPPED,
    }
)
_STATUS_NOT_FOUND = 404
_STATUS_RATE_LIMITED = 429
_STATUS_SERVER_ERROR = 500

# JSON from the provider is untyped by nature; it is narrowed in this module.
type _Json = Any


@dataclass(slots=True)
class _TokenSession:
    """The credentials of one caller and the bearer token obtained for them."""

    credentials: Credentials
    token: str | None = field(default=None, repr=False)


class ContaboProvider:
    """Reads data centers, images and instances from the Contabo API.

    ``credentials`` must hold the four names of ``ProviderKind.CONTABO``. The
    API address of every call comes from the method arguments. ``auth_url`` is
    the token endpoint; a test may point it at a mock. ``verify`` is ``True``
    (system CAs), a CA bundle path, or an explicit ``False``.
    ``restart_settle_seconds`` is how long after a RESTART request the
    instance may be reported as restarted; ``clock`` gives the time stamped on
    operations. Both are options so a test controls them.
    """

    def __init__(
        self,
        credentials: Credentials,
        auth_url: str = DEFAULT_AUTH_URL,
        verify: bool | str = True,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        restart_settle_seconds: float = DEFAULT_RESTART_SETTLE_SECONDS,
        clock: _power.Clock = _power.utc_now,
    ) -> None:
        _require_all_names(credentials)
        self._session = _TokenSession(credentials)
        self._auth_url = auth_url
        self._verify = verify
        self._timeout = timeout
        self._restart_settle = timedelta(seconds=restart_settle_seconds)
        self._clock = clock

    def __repr__(self) -> str:
        return (
            f"ContaboProvider(auth_url={self._auth_url!r}, "
            f"verify={self._verify!r}, timeout={self._timeout!r})"
        )

    def validate_access(self, address: str, credentials: Credentials) -> AccessResult:
        """Request a token, then list one instance; never raises.

        A failed result names the step that failed. The token obtained here is
        not kept: it belongs to ``credentials``, not to this adapter.
        """
        session = _TokenSession(credentials)
        try:
            self._fetch_token(session)
        except (ProviderError, MissingCredentialError) as error:
            return AccessResult(False, f"token request failed: {error}")
        try:
            self._get_body(session, address, INSTANCES_PATH, {"size": 1})
        except ProviderError as error:
            return AccessResult(False, f"instance list request failed: {error}")
        return AccessResult(True)

    def discover_capabilities(
        self, configuration: ProviderConfiguration
    ) -> frozenset[Capability]:
        """Return the read, the four power, provision, remove, backup and restore
        capabilities."""
        return frozenset(
            Capability(name)
            for name in (
                capabilities.LIST_SERVERS,
                capabilities.LIST_SERVER_OPTIONS,
                capabilities.INSPECT_SERVER,
                *capabilities.ACTION_CAPABILITY.values(),
            )
        )

    def list_server_options(
        self, configuration: ProviderConfiguration, option_kind: str
    ) -> tuple[ServerOption, ...]:
        """Return ``region`` (data center), ``image`` or ``product`` options.

        A region value is the data center slug, or its name when it has no
        slug. An image value is the ``imageId`` and a product value the
        ``productId``, which is what the create request needs. Raises
        UnknownOptionKindError for any other kind.
        """
        if option_kind == OPTION_KIND_REGION:
            items = self._get_pages(configuration.address, DATA_CENTERS_PATH)
            return _options(OPTION_KIND_REGION, items, ("slug", "name"))
        if option_kind == OPTION_KIND_IMAGE:
            items = self._get_pages(configuration.address, IMAGES_PATH)
            return _options(OPTION_KIND_IMAGE, items, ("imageId",))
        if option_kind == OPTION_KIND_PRODUCT:
            items = self._get_pages(configuration.address, PRODUCTS_PATH)
            return _options(OPTION_KIND_PRODUCT, items, ("productId",))
        raise UnknownOptionKindError(
            f"Contabo offers no options of kind {option_kind!r}; valid kinds: "
            + ", ".join(ProviderKind.CONTABO.option_kinds)
        )

    def list_servers(self, configuration: ProviderConfiguration) -> tuple[Server, ...]:
        """Return every compute instance of the account."""
        items = self._get_pages(configuration.address, INSTANCES_PATH)
        return tuple(_to_server(item) for item in items if "instanceId" in item)

    def get_server(
        self, configuration: ProviderConfiguration, server_identifier: str
    ) -> Server:
        """Return one instance by its numeric id; raises ServerNotFoundError."""
        text = server_identifier.strip()
        if not (text.isascii() and text.isdecimal()):
            raise ServerNotFoundError(f"No server {server_identifier!r} at Contabo")
        return _to_server(self._instance_item(configuration, server_identifier))

    def _instance_item(
        self, configuration: ProviderConfiguration, server_identifier: str
    ) -> Mapping[str, _Json]:
        """Read one instance as the provider reports it, with its extra fields."""
        text = server_identifier.strip()
        if not (text.isascii() and text.isdecimal()):
            raise ServerNotFoundError(f"No server {server_identifier!r} at Contabo")
        response = self._authorised_get(
            self._session,
            configuration.address,
            f"{INSTANCES_PATH}/{int(text)}",
            {},
        )
        if response.status_code == _STATUS_NOT_FOUND:
            raise ServerNotFoundError(f"No server {server_identifier!r} at Contabo")
        items = _data_items(_body(response))
        if not items:
            raise ServerNotFoundError(f"No server {server_identifier!r} at Contabo")
        return items[0]

    def request_power(
        self,
        configuration: ProviderConfiguration,
        server_identifier: str,
        action: PowerAction,
    ) -> Operation:
        """POST one action of an instance; Contabo gives no task reference.

        A provider failure (401 after renewal, 403, 429, 5xx, unreachable)
        gives a FAILED operation. Raises ServerNotFoundError when the
        identifier is not a number or the instance does not exist, and
        PowerActionUnsupportedError for PROVISION and REMOVE, which are not
        power actions.
        """
        if action not in _POWER_ACTION_PATH:
            raise PowerActionUnsupportedError(f"{action.value} is not a power action")
        text = server_identifier.strip()
        if not (text.isascii() and text.isdecimal()):
            raise ServerNotFoundError(f"No server {server_identifier!r} at Contabo")
        started_at = self._clock()
        path = f"{INSTANCES_PATH}/{int(text)}/actions/{_POWER_ACTION_PATH[action]}"
        try:
            # Look the instance up first, so a write is never sent for an id
            # that does not exist (the same guarantee as the Proxmox adapter).
            self.get_server(configuration, server_identifier)
            response = self._authorised_post(self._session, configuration.address, path)
            if response.status_code == _STATUS_NOT_FOUND:
                raise ServerNotFoundError(f"No server {server_identifier!r} at Contabo")
            _body(response, _ACCEPTED_WRITE_STATUSES)
        except ServerNotFoundError:
            raise
        except ProviderError as error:
            return _power.failed_operation(
                configuration, server_identifier, action, started_at, str(error)
            )
        return _power.running_operation(
            configuration, server_identifier, action, started_at, None
        )

    def value_rules(
        self, configuration: ProviderConfiguration
    ) -> tuple[ValueRule, ...]:
        """Return the values an instance creation needs."""
        return _provision.value_rules()

    def reserve_identifier(self, configuration: ProviderConfiguration) -> str | None:
        """Contabo chooses the instance id itself: always None."""
        return None

    def cost_consequence(
        self, configuration: ProviderConfiguration, desired: DesiredConfiguration
    ) -> str | None:
        """Name the contract period in months and the commitment it makes."""
        return _provision.cost_consequence(desired)

    def request_provision(
        self,
        configuration: ProviderConfiguration,
        desired: DesiredConfiguration,
        server_name: str,
        reserved_identifier: str | None,
    ) -> Operation:
        """POST the one instance creation; the answer names the new instance.

        A provider failure (401 after renewal, 403, 429, 4xx, 5xx, unreachable)
        gives a FAILED operation. When the provider accepts the request but the
        answer names no instance, the operation stays RUNNING without a server
        identifier and says so: the instance may exist, so it is not FAILED.
        """
        started_at = self._clock()
        try:
            body = _provision.build_body(desired, server_name)
            response = self._authorised_post(
                self._session, configuration.address, INSTANCES_PATH, body
            )
            instance_id = _created_instance_id(
                _body(response, _ACCEPTED_WRITE_STATUSES)
            )
        except (ProviderTimeoutError, ProviderServiceError) as error:
            return _power.unknown_operation(
                configuration,
                None,
                PowerAction.PROVISION,
                started_at,
                f"the request may have reached the provider: {error}",
            )
        except ProviderError as error:
            return _power.failed_operation(
                configuration, None, PowerAction.PROVISION, started_at, str(error)
            )
        running = _power.running_operation(
            configuration, instance_id, PowerAction.PROVISION, started_at, None
        )
        if instance_id is None:
            return replace(running, detail=_NO_INSTANCE_NAMED)
        return running

    def removal_kind(self, configuration: ProviderConfiguration) -> RemovalKind:
        """Contabo cancels, it does not delete: always CANCEL."""
        return RemovalKind.CANCEL

    def removal_block(
        self, configuration: ProviderConfiguration, server: Server
    ) -> RemovalBlock | None:
        """Return ALREADY_REMOVED for a cancelled instance, else None.

        A running instance is not blocked: Contabo cancels it as it is.
        """
        return _removal.block(server)

    def consequence(self, configuration: ProviderConfiguration, server: Server) -> str:
        """State that a cancellation is requested and the contract runs on."""
        return _removal.consequence(server)

    def request_removal(
        self, configuration: ProviderConfiguration, server: Server
    ) -> Operation:
        """POST the one cancellation; the date it reports is kept in the detail.

        The operation is RUNNING until the instance is read as ``cancelled``;
        its detail holds the cancel date the answer gave, or says that none was
        reported. A rejection (401 after renewal, 403, 429, 4xx) gives a FAILED
        operation; a timeout or a 5xx gives an UNKNOWN one, because the
        cancellation may have been recorded. Raises ServerNotFoundError when
        the identifier is not a number or the instance does not exist.
        """
        started_at = self._clock()
        identifier = server.server_identifier
        text = identifier.strip()
        if not (text.isascii() and text.isdecimal()):
            raise ServerNotFoundError(f"No server {identifier!r} at Contabo")
        path = _removal.cancel_path(INSTANCES_PATH, int(text))
        try:
            response = self._authorised_post(self._session, configuration.address, path)
            if response.status_code == _STATUS_NOT_FOUND:
                raise ServerNotFoundError(f"No server {identifier!r} at Contabo")
            items = _data_items(_body(response, _ACCEPTED_WRITE_STATUSES))
        except ServerNotFoundError:
            raise
        except (ProviderTimeoutError, ProviderServiceError) as error:
            return _power.unknown_operation(
                configuration,
                identifier,
                PowerAction.REMOVE,
                started_at,
                f"the request may have reached the provider: {error}",
            )
        except ProviderError as error:
            return _power.failed_operation(
                configuration, identifier, PowerAction.REMOVE, started_at, str(error)
            )
        running = _power.running_operation(
            configuration, identifier, PowerAction.REMOVE, started_at, None
        )
        date = _removal.cancel_date(items[0]) if items else None
        return replace(running, detail=_removal.requested_detail(date))

    def backup_kind(self, configuration: ProviderConfiguration) -> BackupKind:
        """A Contabo backup is a snapshot held by the provider: always SNAPSHOT."""
        return BackupKind.SNAPSHOT

    def backup_storages(
        self, configuration: ProviderConfiguration, server: Server
    ) -> tuple[str, ...]:
        """Contabo has no storage to choose: always empty."""
        return ()

    def backup_block(
        self,
        configuration: ProviderConfiguration,
        server: Server,
        storage: str | None,
    ) -> BackupBlock | None:
        """Return STORAGE_UNSUITABLE for a named storage and CANCELLED for a
        cancelled instance, else None; sends no request."""
        return _backup.backup_block(server, storage)

    def restore_block(
        self, configuration: ProviderConfiguration, server: Server, backup: Backup
    ) -> BackupBlock | None:
        """Return CANCELLED for a cancelled instance and MISMATCH for a snapshot
        the instance does not list; reads only."""
        if server.state == _backup.STATUS_CANCELLED:
            return BackupBlock.CANCELLED
        items = self._snapshot_items(configuration, server.server_identifier)
        listed = {_backup.snapshot_id(item) for item in items}
        return None if backup.backup_id in listed else BackupBlock.MISMATCH

    def backup_consequence(
        self,
        configuration: ProviderConfiguration,
        server: Server,
        storage: str | None,
    ) -> str:
        """State that a snapshot is not an independent backup and its cost is not
        stated."""
        return _backup.backup_consequence(server)

    def restore_effect(
        self, configuration: ProviderConfiguration, server: Server, backup: Backup
    ) -> RestoreEffect:
        """List the newer snapshots the rollback deletes and say it cannot be undone.

        Raises ProviderError when a snapshot has no date, because then it cannot
        be told which ones are newer.
        """
        items = self._snapshot_items(configuration, server.server_identifier)
        if any(_backup.is_undated(item) for item in items):
            raise ProviderError(
                "a snapshot has no creation date, so the newer ones cannot be told"
            )
        deleted = _backup.newer_than(backup, _snapshots_of(items))
        return RestoreEffect(
            _backup.restore_consequence(server, backup, deleted), deleted
        )

    def request_backup(
        self,
        configuration: ProviderConfiguration,
        server: Server,
        storage: str | None,
    ) -> Operation:
        """POST the one snapshot creation; the snapshot is found again by its
        description, which holds the operation identifier.

        A named storage is refused (FAILED, nothing sent). A rejection (401 after
        renewal, 403, 429, 4xx) gives a FAILED operation; a timeout or a 5xx gives
        an UNKNOWN one, because the snapshot may have been made. Raises
        ServerNotFoundError when the identifier is not a number or the instance
        does not exist.
        """
        started_at = self._clock()
        identifier = server.server_identifier
        number = _instance_number(identifier)
        operation_id = _power.new_operation_id()
        block = _backup.backup_block(server, storage)
        if block is not None:
            return _power.failed_operation(
                configuration,
                identifier,
                PowerAction.BACKUP,
                started_at,
                f"the backup was not sent: {block.value}",
                operation_id,
            )
        path = _backup.snapshots_path(INSTANCES_PATH, number)
        body = _backup.snapshot_body(started_at, operation_id)
        try:
            response = self._authorised_post(
                self._session, configuration.address, path, body
            )
            if response.status_code == _STATUS_NOT_FOUND:
                raise ServerNotFoundError(f"No server {identifier!r} at Contabo")
            items = _data_items(_body(response, _ACCEPTED_WRITE_STATUSES))
        except ServerNotFoundError:
            raise
        except (ProviderTimeoutError, ProviderServiceError) as error:
            return _power.unknown_operation(
                configuration,
                identifier,
                PowerAction.BACKUP,
                started_at,
                f"the request may have reached the provider: {error}",
                operation_id,
            )
        except ProviderError as error:
            return _power.failed_operation(
                configuration,
                identifier,
                PowerAction.BACKUP,
                started_at,
                str(error),
                operation_id,
            )
        snapshot = _backup.snapshot_id(items[0]) if items else None
        return _power.running_operation(
            configuration,
            identifier,
            PowerAction.BACKUP,
            started_at,
            snapshot,
            operation_id,
        )

    def request_restore(
        self, configuration: ProviderConfiguration, server: Server, backup: Backup
    ) -> Operation:
        """POST the one rollback to a snapshot, with an empty body.

        The snapshot identifier is the reference. Nothing is sent, and the
        operation is FAILED with the reason, for a cancelled instance or a
        snapshot the instance does not list. A rejection (401 after renewal, 403,
        429, 4xx) gives a FAILED operation; a timeout or a 5xx gives an UNKNOWN
        one, because the rollback may have started. Raises ServerNotFoundError
        when the identifier is not a number.
        """
        started_at = self._clock()
        identifier = server.server_identifier
        number = _instance_number(identifier)
        operation_id = _power.new_operation_id()
        try:
            block = self.restore_block(configuration, server, backup)
            if block is not None:
                raise ProviderError(f"the restore was not sent: {block.value}")
        except ProviderError as error:
            return _power.failed_operation(
                configuration,
                identifier,
                PowerAction.RESTORE,
                started_at,
                str(error),
                operation_id,
            )
        path = _backup.rollback_path(INSTANCES_PATH, number, backup.backup_id)
        try:
            response = self._authorised_post(self._session, configuration.address, path)
            _body(response, _ACCEPTED_WRITE_STATUSES)
        except (ProviderTimeoutError, ProviderServiceError) as error:
            return _power.unknown_operation(
                configuration,
                identifier,
                PowerAction.RESTORE,
                started_at,
                f"the request may have reached the provider: {error}",
                operation_id,
            )
        except ProviderError as error:
            return _power.failed_operation(
                configuration,
                identifier,
                PowerAction.RESTORE,
                started_at,
                str(error),
                operation_id,
            )
        return _power.running_operation(
            configuration,
            identifier,
            PowerAction.RESTORE,
            started_at,
            backup.backup_id,
            operation_id,
        )

    def list_backups(
        self, configuration: ProviderConfiguration, server: Server
    ) -> tuple[Backup, ...]:
        """Return the snapshots of the instance, oldest first.

        A snapshot with no creation date is left out: it cannot be placed in
        time. One whose name starts with ``iacnf-`` is marked ``made_by_client``.
        """
        items = self._snapshot_items(configuration, server.server_identifier)
        return _snapshots_of(items)

    def find_backup(
        self, configuration: ProviderConfiguration, operation: Operation
    ) -> Backup | None:
        """Return the snapshot whose description names the operation, or None."""
        if operation.server_identifier is None:
            return None
        items = self._snapshot_items(configuration, operation.server_identifier)
        for item in items:
            if _backup.carries_operation(item, operation.operation_id):
                return _backup.to_backup(item)
        return None

    def _snapshot_items(
        self, configuration: ProviderConfiguration, identifier: str
    ) -> list[Mapping[str, _Json]]:
        path = _backup.snapshots_path(INSTANCES_PATH, _instance_number(identifier))
        return self._get_pages(configuration.address, path)

    def _judge_backup(
        self, configuration: ProviderConfiguration, operation: Operation, status: str
    ) -> Operation:
        """Succeed when the snapshot is listed and the instance status is final."""
        if status not in _backup.FINAL_STATUSES or operation.server_identifier is None:
            return operation
        items = self._snapshot_items(configuration, operation.server_identifier)
        listed = [
            i for i in items if _backup.carries_operation(i, operation.operation_id)
        ]
        if not listed:
            return operation
        return _power.end_operation(
            operation, OperationState.SUCCEEDED, self._clock, "the snapshot is listed"
        )

    def _judge_restore(
        self, configuration: ProviderConfiguration, operation: Operation, status: str
    ) -> Operation:
        """Succeed when the instance status is final and no snapshot newer than the
        target is listed; fail when the target itself is no longer listed."""
        target_id = operation.provider_reference
        if status not in _backup.FINAL_STATUSES or operation.server_identifier is None:
            return operation
        items = self._snapshot_items(configuration, operation.server_identifier)
        if any(_backup.is_undated(item) for item in items):
            raise ProviderError(
                "a snapshot has no creation date; the rollback cannot be judged"
            )
        snapshots = _snapshots_of(items)
        target = next((s for s in snapshots if s.backup_id == target_id), None)
        if target is None:
            return _power.end_operation(
                operation,
                OperationState.FAILED,
                self._clock,
                "the snapshot is no longer listed",
            )
        if _backup.newer_than(target, snapshots):
            return operation
        return _power.end_operation(
            operation,
            OperationState.SUCCEEDED,
            self._clock,
            _backup.restored_detail(status),
        )

    def operation_state(
        self, configuration: ProviderConfiguration, operation: Operation
    ) -> Operation:
        """Read the instance status once and judge it against the action.

        A backup succeeds when a snapshot with the operation's description is
        listed and the instance is ``running`` or ``stopped``; a restore succeeds
        when the instance is ``running`` or ``stopped`` and no snapshot newer than
        the target is listed. Otherwise either is still RUNNING: Contabo gives
        neither a task nor a snapshot status (B20), and the caller's time limit
        ends it as unknown, never as success. ``error`` fails.

        A removal succeeds when the instance is ``cancelled``, with the cancel
        date the provider reports in the detail (or the words that none was
        reported); ``error`` fails; any other status is still RUNNING.

        A provision succeeds when the instance is ``running``, fails when its
        status is ``error`` and is otherwise still RUNNING (``provisioning``,
        ``installing``, ``unknown`` and the rest are not final). A provision
        without a server identifier cannot be read and stays as it is. The
        rest of this text is about a power action.

        START, SHUTDOWN and STOP succeed when the status equals the target;
        ``error`` fails; any other status is still RUNNING. RESTART succeeds
        only when asked at or after the settle time with status ``running``,
        and its detail says the transition was not observed.
        """
        if operation.has_ended or operation.server_identifier is None:
            return operation
        item = self._instance_item(configuration, operation.server_identifier)
        status = _to_server(item).state
        if status == _STATUS_ERROR:
            return _power.end_operation(
                operation,
                OperationState.FAILED,
                self._clock,
                f"the instance reported status {status!r}",
            )
        if operation.action is PowerAction.PROVISION:
            return self._judge_provision(operation, status)
        if operation.action is PowerAction.REMOVE:
            return self._judge_removal(operation, status, item)
        if operation.action is PowerAction.BACKUP:
            return self._judge_backup(configuration, operation, status)
        if operation.action is PowerAction.RESTORE:
            return self._judge_restore(configuration, operation, status)
        if operation.action is PowerAction.RESTART:
            return self._judge_restart(operation, status)
        if status == _TARGET_STATUS[operation.action]:
            return _power.end_operation(
                operation, OperationState.SUCCEEDED, self._clock, None
            )
        return operation

    def _judge_provision(self, operation: Operation, status: str) -> Operation:
        if status == _STATUS_RUNNING:
            return _power.end_operation(
                operation, OperationState.SUCCEEDED, self._clock, None
            )
        return operation

    def _judge_removal(
        self, operation: Operation, status: str, item: Mapping[str, _Json]
    ) -> Operation:
        if status == _removal.STATUS_CANCELLED:
            detail = _removal.cancelled_detail(_removal.cancel_date(item))
            return _power.end_operation(
                operation, OperationState.SUCCEEDED, self._clock, detail
            )
        return operation

    def _judge_restart(self, operation: Operation, status: str) -> Operation:
        settled = self._clock() >= operation.started_at + self._restart_settle
        if settled and status == _STATUS_RUNNING:
            return _power.end_operation(
                operation,
                OperationState.SUCCEEDED,
                self._clock,
                _RESTART_NOT_OBSERVED,
            )
        return operation

    def _get_pages(self, address: str, path: str) -> list[Mapping[str, _Json]]:
        items: list[Mapping[str, _Json]] = []
        for page in range(1, MAX_PAGES + 1):
            body = self._get_body(
                self._session, address, path, {"page": page, "size": PAGE_SIZE}
            )
            data = _data_items(body)
            items.extend(data)
            if not data or page >= _total_pages(body):
                return items
        raise ProviderPageLimitError(
            f"Contabo listing {path} has more than {MAX_PAGES} pages; not read"
        )

    def _get_body(
        self,
        session: _TokenSession,
        address: str,
        path: str,
        params: Mapping[str, int],
    ) -> Mapping[str, _Json]:
        return _body(self._authorised_get(session, address, path, params))

    def _authorised_get(
        self,
        session: _TokenSession,
        address: str,
        path: str,
        params: Mapping[str, int],
    ) -> httpx.Response:
        """GET with the session's token; renew it once after a 401."""
        url = _http.base_url(address) + path
        return self._authorised(
            session,
            lambda client, headers: client.get(url, params=params, headers=headers),
        )

    def _authorised_post(
        self,
        session: _TokenSession,
        address: str,
        path: str,
        body: Mapping[str, _Json] | None = None,
    ) -> httpx.Response:
        """POST a write; renew the token once after a 401.

        A power action is documented without a request body, so only the JSON
        content type is sent; a creation sends ``body`` as JSON. A 401 means
        the request was not processed, so repeating it once with the new token
        cannot act twice.
        """
        url = _http.base_url(address) + path
        return self._authorised(
            session,
            lambda client, headers: (
                client.post(
                    url, headers={**headers, "Content-Type": "application/json"}
                )
                if body is None
                else client.post(url, headers=headers, json=body)
            ),
        )

    def _authorised(
        self,
        session: _TokenSession,
        call: Callable[[httpx.Client, dict[str, str]], httpx.Response],
    ) -> httpx.Response:
        if session.token is None:
            self._fetch_token(session)
        response = self._send_with_token(session, call)
        if response.status_code != 401:
            return response
        self._fetch_token(session)
        return self._send_with_token(session, call)

    def _send_with_token(
        self,
        session: _TokenSession,
        call: Callable[[httpx.Client, dict[str, str]], httpx.Response],
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {session.token}",
            "x-request-id": str(uuid.uuid4()),
        }
        return self._send(lambda client: call(client, headers))

    def _fetch_token(self, session: _TokenSession) -> None:
        credentials = session.credentials
        form = {
            "grant_type": "password",
            "client_id": credentials.get("client_id"),
            "client_secret": credentials.get("client_secret"),
            "username": credentials.get("api_user"),
            "password": credentials.get("api_password"),
        }
        session.token = None
        response = self._send(lambda client: client.post(self._auth_url, data=form))
        if response.status_code in (400, 401, 403):
            raise ProviderAuthenticationError(
                f"Contabo answered {response.status_code} at the token request: "
                "the four credential values were rejected"
            )
        token = _body(response).get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderError("the Contabo token answer has no access_token")
        session.token = token

    def _send(self, call: Callable[[httpx.Client], httpx.Response]) -> httpx.Response:
        try:
            with httpx.Client(
                verify=_http.tls_verify(self._verify), timeout=self._timeout
            ) as client:
                return call(client)
        except httpx.TransportError as error:
            raise _http.unreachable_error(error, self._timeout) from error


def _instance_number(identifier: str) -> int:
    """Return the numeric id of an instance; raise ServerNotFoundError otherwise."""
    text = identifier.strip()
    if not (text.isascii() and text.isdecimal()):
        raise ServerNotFoundError(f"No server {identifier!r} at Contabo")
    return int(text)


def _snapshots_of(items: list[Mapping[str, _Json]]) -> tuple[Backup, ...]:
    """Return the dated snapshots of a listing as backups, oldest first."""
    found = [b for item in items if (b := _backup.to_backup(item)) is not None]
    return tuple(sorted(found, key=lambda b: (b.created_at, b.backup_id)))


def _require_all_names(credentials: Credentials) -> None:
    for name in ProviderKind.CONTABO.credential_names:
        credentials.get(name)


def _body(
    response: httpx.Response, accepted: frozenset[int] = frozenset({200})
) -> Mapping[str, _Json]:
    status = response.status_code
    if status in (401, 403):
        raise ProviderAuthenticationError(
            f"Contabo answered {status}: token rejected or not permitted"
        )
    if status == _STATUS_RATE_LIMITED:
        raise ProviderRateLimitError(
            "Contabo answered 429: rate limit reached, try again later"
        )
    if status >= _STATUS_SERVER_ERROR:
        raise ProviderServiceError(f"Contabo answered {status}: service error")
    if status not in accepted:
        raise ProviderError(f"Contabo answered with status {status}")
    try:
        body = response.json()
    except ValueError as error:
        raise ProviderError("Contabo answered with invalid JSON") from error
    if not isinstance(body, Mapping):
        raise ProviderError("the Contabo answer has an unexpected shape")
    return body


def _created_instance_id(body: Mapping[str, _Json]) -> str | None:
    """Return the id of the instance a creation answer names, if it names one."""
    for item in _data_items(body):
        identifier = item.get("instanceId")
        if isinstance(identifier, int) and not isinstance(identifier, bool):
            return str(identifier)
    return None


def _data_items(body: Mapping[str, _Json]) -> list[Mapping[str, _Json]]:
    data = body.get("data")
    if not isinstance(data, list):
        raise ProviderError("the Contabo answer has no data array")
    return [item for item in data if isinstance(item, Mapping)]


def _total_pages(body: Mapping[str, _Json]) -> int:
    pagination = body.get("_pagination")
    if isinstance(pagination, Mapping):
        total = pagination.get("totalPages")
        if isinstance(total, int | float) and not isinstance(total, bool):
            return int(total)
    return 1


def _options(
    kind: str, items: list[Mapping[str, _Json]], keys: tuple[str, ...]
) -> tuple[ServerOption, ...]:
    options: list[ServerOption] = []
    for item in items:
        value = next((_text(item.get(key)) for key in keys if _text(item.get(key))), "")
        if value:
            options.append(ServerOption(kind, value))
    return tuple(options)


def _text(value: _Json) -> str:
    return value.strip() if isinstance(value, str) else ""


def _to_server(item: Mapping[str, _Json]) -> Server:
    identifier = str(item.get("instanceId"))
    return Server(
        server_identifier=identifier,
        name=_text(item.get("displayName")) or _text(item.get("name")) or identifier,
        state=_text(item.get("status")) or _UNKNOWN,
        size=_size_text(item.get("cpuCores"), item.get("ramMb")),
        addresses=_addresses(item),
        managed=False,
    )


def _size_text(cpu: _Json, memory_mib: _Json) -> str:
    parts: list[str] = []
    if _is_number(cpu):
        parts.append(f"{cpu:g} vCPU")
    if _is_number(memory_mib):
        parts.append(f"{int(memory_mib)} MiB")
    return ", ".join(parts) or _UNKNOWN


def _is_number(value: _Json) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _addresses(item: Mapping[str, _Json]) -> tuple[str, ...]:
    found: list[str] = []
    config = item.get("ipConfig")
    if isinstance(config, Mapping):
        found.extend(_ip(config.get(version)) for version in ("v4", "v6"))
    extra = item.get("additionalIps")
    if isinstance(extra, list):
        found.extend(
            _ip(entry.get("v4")) for entry in extra if isinstance(entry, Mapping)
        )
    return tuple(dict.fromkeys(address for address in found if address))


def _ip(block: _Json) -> str:
    return _text(block.get("ip")) if isinstance(block, Mapping) else ""
