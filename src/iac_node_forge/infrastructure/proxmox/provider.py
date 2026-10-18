"""ProviderPort, ServerPowerPort and ServerProvisionPort for Proxmox VE.

Authentication is by Proxmox VE API token only in this slice: the secret of
the credentials is the full token string ``USER@REALM!TOKENID=SECRET`` and is
sent as ``Authorization: PVEAPIToken=<secret>``. Ticket login
(``POST /access/ticket``) is out of scope. Every request is a GET except the
four power POSTs of ``_POWER_VERB`` (``status/start``, ``shutdown``, ``stop``
and ``reboot`` of one VM or container) and the two creations of
``_provision`` (``POST /nodes/{node}/lxc`` and ``/qemu``); no other write path
exists here (ADR-0006, ADR-0007). The power POSTs need the ``VM.PowerMgmt``
permission and a creation needs ``VM.Allocate``.
"""

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any
from urllib.parse import quote

import httpx

from iac_node_forge.application import capabilities
from iac_node_forge.application.errors import (
    PowerActionUnsupportedError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderTimeoutError,
    ServerNotFoundError,
    UnknownOptionKindError,
)
from iac_node_forge.domain.access_result import AccessResult
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.operation import Operation, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_option import ServerOption
from iac_node_forge.domain.value_rule import ValueRule
from iac_node_forge.infrastructure import _http, _power
from iac_node_forge.infrastructure.proxmox import _provision

API_PREFIX = "/api2/json"
OPTION_KIND_NODE = "node"
OPTION_KIND_STORAGE = "storage"
OPTION_KIND_TEMPLATE = "template"
CAPABILITY_LIST_SERVERS = capabilities.LIST_SERVERS
CAPABILITY_LIST_SERVER_OPTIONS = capabilities.LIST_SERVER_OPTIONS
CAPABILITY_INSPECT_SERVER = capabilities.INSPECT_SERVER
DEFAULT_TIMEOUT_SECONDS = 10.0
_BYTES_PER_MIB = 1024 * 1024
_UNKNOWN_STATE = "unknown"
_POWER_PERMISSION = "VM.PowerMgmt"
# Content types of a storage that can hold what a creation starts from: a
# container template (lxc) or an installation image (qemu).
_TEMPLATE_CONTENT_TYPES = ("vztmpl", "iso")
_TASK_RUNNING = "running"
_TASK_STOPPED = "stopped"
_EXIT_OK = "OK"
_NO_EXIT_STATUS = "the task ended without an exit status"
_GUEST_TYPES = frozenset({"qemu", "lxc"})
_NODE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*")
_VMID = re.compile(r"[0-9]+")
_UPID_PREFIX = "UPID:"
# The task identifier keeps its colons, at sign and exclamation mark in a path.
_UPID_SAFE = ":@!"

# The only write requests this adapter can send, one verb per power action.
_POWER_VERB: Mapping[PowerAction, str] = MappingProxyType(
    {
        PowerAction.START: "start",
        PowerAction.SHUTDOWN: "shutdown",
        PowerAction.STOP: "stop",
        PowerAction.RESTART: "reboot",
    }
)

# JSON from the provider is untyped by nature; it is narrowed in this module.
type _Json = Any


class ProxmoxProvider:
    """Reads and powers virtual machines and containers of a Proxmox VE API.

    ``credentials`` is injected at construction because the ProviderPort
    methods other than ``validate_access`` receive only a configuration, and a
    configuration never holds a secret. The address of every call comes from
    the method arguments, so one adapter serves any configuration.

    ``verify`` is ``True`` (system CAs), a CA bundle path, or an explicit
    ``False`` for a self-signed certificate; it is never disabled silently.
    ``clock`` gives the time stamped on operations; a test replaces it.
    """

    def __init__(
        self,
        credentials: Credentials,
        verify: bool | str = True,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        clock: _power.Clock = _power.utc_now,
    ) -> None:
        self._credentials = credentials
        self._verify = verify
        self._timeout = timeout
        self._clock = clock

    def __repr__(self) -> str:
        return f"ProxmoxProvider(verify={self._verify!r}, timeout={self._timeout!r})"

    def validate_access(self, address: str, credentials: Credentials) -> AccessResult:
        """Check the address and token with ``GET /version``; never raises."""
        try:
            self._get(address, "/version", credentials)
        except ProviderError as error:
            return AccessResult(False, str(error))
        return AccessResult(True)

    def discover_capabilities(
        self, configuration: ProviderConfiguration
    ) -> frozenset[Capability]:
        """Return the read, the four power and the provision capabilities."""
        return frozenset(
            Capability(name)
            for name in (
                CAPABILITY_LIST_SERVERS,
                CAPABILITY_LIST_SERVER_OPTIONS,
                CAPABILITY_INSPECT_SERVER,
                *capabilities.ACTION_CAPABILITY.values(),
            )
        )

    def list_server_options(
        self, configuration: ProviderConfiguration, option_kind: str
    ) -> tuple[ServerOption, ...]:
        """Return ``node``, ``storage`` or ``template`` options.

        A storage option value is ``<node>/<storage>``, because storage is
        read per node and a name such as ``local`` exists on every node. A
        template option value is ``<node>/<volume id>`` of a container template
        or an ISO image found in the content of the node's storages.

        Raises UnknownOptionKindError for any other kind.
        """
        if option_kind == OPTION_KIND_NODE:
            return tuple(
                ServerOption(OPTION_KIND_NODE, name)
                for name in self._node_names(configuration)
            )
        if option_kind == OPTION_KIND_STORAGE:
            return self._storage_options(configuration)
        if option_kind == OPTION_KIND_TEMPLATE:
            return self._template_options(configuration)
        raise UnknownOptionKindError(
            f"Proxmox VE offers no options of kind {option_kind!r}; valid kinds: "
            + ", ".join(ProviderKind.PROXMOX_VE.option_kinds)
        )

    def list_servers(self, configuration: ProviderConfiguration) -> tuple[Server, ...]:
        """Return the virtual machines and containers of the cluster."""
        resources = self._get_data(configuration.address, "/cluster/resources?type=vm")
        return tuple(
            _to_server(item)
            for item in _as_list(resources)
            if isinstance(item, Mapping) and "vmid" in item
        )

    def get_server(
        self, configuration: ProviderConfiguration, server_identifier: str
    ) -> Server:
        """Return one server by vmid; raises ServerNotFoundError when absent."""
        for server in self.list_servers(configuration):
            if server.server_identifier == server_identifier:
                return server
        raise ServerNotFoundError(f"No server {server_identifier!r} at the provider")

    def request_power(
        self,
        configuration: ProviderConfiguration,
        server_identifier: str,
        action: PowerAction,
    ) -> Operation:
        """Send one power POST; the answer's task identifier is the reference.

        A provider failure (401, 403, 5xx, unreachable ...) gives a FAILED
        operation; a 403 names the ``VM.PowerMgmt`` permission. Raises
        ServerNotFoundError when no VM or container has the identifier and
        PowerActionUnsupportedError for PROVISION, which is not a power action.
        """
        if action not in _POWER_VERB:
            raise PowerActionUnsupportedError(f"{action.value} is not a power action")
        started_at = self._clock()
        try:
            node, guest_type = self._locate(configuration, server_identifier)
            path = (
                f"/nodes/{quote(node, safe='')}/{guest_type}/{server_identifier}"
                f"/status/{_POWER_VERB[action]}"
            )
            upid = self._post(configuration.address, path, _POWER_PERMISSION)
        except ServerNotFoundError:
            raise
        except ProviderError as error:
            return _power.failed_operation(
                configuration, server_identifier, action, started_at, str(error)
            )
        return _power.running_operation(
            configuration, server_identifier, action, started_at, upid
        )

    def value_rules(
        self, configuration: ProviderConfiguration
    ) -> tuple[ValueRule, ...]:
        """Return the values a container or virtual machine creation needs."""
        return _provision.value_rules()

    def reserve_identifier(self, configuration: ProviderConfiguration) -> str | None:
        """Return the next free VM or container id from ``/cluster/nextid``."""
        data = self._get_data(configuration.address, "/cluster/nextid")
        vmid = str(data) if isinstance(data, str | int) else ""
        if not _VMID.fullmatch(vmid):
            raise ProviderError("the provider gave no usable next identifier")
        return vmid

    def cost_consequence(
        self, configuration: ProviderConfiguration, desired: DesiredConfiguration
    ) -> str | None:
        """Proxmox VE imposes no commitment: always None."""
        return None

    def request_provision(
        self,
        configuration: ProviderConfiguration,
        desired: DesiredConfiguration,
        server_name: str,
        reserved_identifier: str | None,
    ) -> Operation:
        """Send one create POST; the answer's task identifier is the reference.

        The new server's identifier is the vmid: ``reserved_identifier`` or,
        when None, the next free id read now. A provider failure gives a FAILED
        operation; a 401 or 403 names the ``VM.Allocate`` permission.
        """
        started_at = self._clock()
        vmid = reserved_identifier
        try:
            if vmid is None:
                vmid = self.reserve_identifier(configuration)
            request = _provision.build_request(desired, server_name, vmid or "")
            upid = self._post(
                configuration.address, request.path, _provision.PERMISSION, request.form
            )
        except ProviderTimeoutError as error:
            return _power.unknown_operation(
                configuration,
                vmid,
                PowerAction.PROVISION,
                started_at,
                f"the request may have reached the provider: {error}",
            )
        except ProviderError as error:
            return _power.failed_operation(
                configuration, None, PowerAction.PROVISION, started_at, str(error)
            )
        return _power.running_operation(
            configuration, vmid, PowerAction.PROVISION, started_at, upid
        )

    def operation_state(
        self, configuration: ProviderConfiguration, operation: Operation
    ) -> Operation:
        """Read the task status once: running, OK, or another exit status.

        The same reading serves a power action and a provision: both are tasks.
        """
        if operation.has_ended:
            return operation
        upid = operation.provider_reference
        if upid is None or not upid.startswith(_UPID_PREFIX):
            raise ProviderError("the operation has no Proxmox VE task identifier")
        node = _upid_node(upid)
        path = (
            f"/nodes/{quote(node, safe='')}/tasks/{quote(upid, safe=_UPID_SAFE)}/status"
        )
        data = self._get_data(configuration.address, path)
        if not isinstance(data, Mapping):
            raise ProviderError("the provider answer has an unexpected shape")
        task_status = data.get("status")
        if task_status == _TASK_RUNNING:
            return operation
        if task_status != _TASK_STOPPED:
            raise ProviderError("the provider gave a task status that is not known")
        exit_status = data.get("exitstatus")
        if exit_status == _EXIT_OK:
            return _power.end_operation(
                operation, OperationState.SUCCEEDED, self._clock, None
            )
        detail = exit_status if isinstance(exit_status, str) else _NO_EXIT_STATUS
        return _power.end_operation(
            operation, OperationState.FAILED, self._clock, detail
        )

    def _locate(
        self, configuration: ProviderConfiguration, server_identifier: str
    ) -> tuple[str, str]:
        """Return the node and guest type of a VM or container by vmid."""
        resources = self._get_data(configuration.address, "/cluster/resources?type=vm")
        for item in _as_list(resources):
            if not isinstance(item, Mapping) or str(item.get("vmid")) != (
                server_identifier
            ):
                continue
            node, guest_type = str(item.get("node", "")), str(item.get("type", ""))
            if (
                _NODE_NAME.fullmatch(node)
                and guest_type in _GUEST_TYPES
                and _VMID.fullmatch(server_identifier)
            ):
                return node, guest_type
            raise ProviderError("the provider gave no usable node or type for it")
        raise ServerNotFoundError(f"No server {server_identifier!r} at the provider")

    def _node_names(self, configuration: ProviderConfiguration) -> tuple[str, ...]:
        nodes = self._get_data(configuration.address, "/nodes")
        return tuple(
            str(item["node"])
            for item in _as_list(nodes)
            if isinstance(item, Mapping) and "node" in item
        )

    def _storage_options(
        self, configuration: ProviderConfiguration
    ) -> tuple[ServerOption, ...]:
        options: list[ServerOption] = []
        for node in self._node_names(configuration):
            storages = self._get_data(configuration.address, f"/nodes/{node}/storage")
            options.extend(
                ServerOption(OPTION_KIND_STORAGE, f"{node}/{item['storage']}")
                for item in _as_list(storages)
                if isinstance(item, Mapping) and "storage" in item
            )
        return tuple(options)

    def _template_options(
        self, configuration: ProviderConfiguration
    ) -> tuple[ServerOption, ...]:
        """List the templates and ISO images of every storage of every node."""
        address = configuration.address
        options: list[ServerOption] = []
        for node in self._node_names(configuration):
            quoted_node = quote(node, safe="")
            for content in _TEMPLATE_CONTENT_TYPES:
                storages = self._get_data(
                    address, f"/nodes/{quoted_node}/storage?content={content}"
                )
                for storage in _as_list(storages):
                    if not isinstance(storage, Mapping) or "storage" not in storage:
                        continue
                    listing = self._get_data(
                        address,
                        f"/nodes/{quoted_node}/storage/"
                        f"{quote(str(storage['storage']), safe='')}"
                        f"/content?content={content}",
                    )
                    options.extend(
                        ServerOption(OPTION_KIND_TEMPLATE, f"{node}/{item['volid']}")
                        for item in _as_list(listing)
                        if isinstance(item, Mapping) and "volid" in item
                    )
        return tuple(options)

    def _get_data(self, address: str, path: str) -> _Json:
        return self._get(address, path, self._credentials)

    def _get(self, address: str, path: str, credentials: Credentials) -> _Json:
        return self._send("GET", address, path, credentials)

    def _post(
        self,
        address: str,
        path: str,
        permission: str,
        form: Mapping[str, str] | None = None,
    ) -> str:
        """POST a write request and return the task identifier.

        A power request has no body; a creation sends its fields as a form.
        ``permission`` is the one a 401 or 403 says the token may lack.
        """
        data = self._send("POST", address, path, self._credentials, form, permission)
        if not isinstance(data, str) or not data.startswith(_UPID_PREFIX):
            raise ProviderError("the provider answer holds no task identifier")
        return data

    def _send(
        self,
        method: str,
        address: str,
        path: str,
        credentials: Credentials,
        form: Mapping[str, str] | None = None,
        permission: str | None = None,
    ) -> _Json:
        url = _http.base_url(address) + API_PREFIX + path
        headers = {"Authorization": f"PVEAPIToken={credentials.get('token')}"}
        try:
            with httpx.Client(
                verify=_http.tls_verify(self._verify), timeout=self._timeout
            ) as client:
                response = client.request(method, url, headers=headers, data=form)
        except httpx.TransportError as error:
            raise _http.unreachable_error(error, self._timeout) from error
        return _payload(response, permission)


def _upid_node(upid: str) -> str:
    """Return the node a task identifier names: ``UPID:<node>:<pid>:...``."""
    parts = upid.split(":")
    node = parts[1] if len(parts) > 1 else ""
    if not _NODE_NAME.fullmatch(node):
        raise ProviderError("the Proxmox VE task identifier is not usable")
    return node


def _payload(response: httpx.Response, permission: str | None = None) -> _Json:
    status = response.status_code
    if status in (401, 403):
        needed = f"; this request needs {permission}" if permission else ""
        raise ProviderAuthenticationError(
            f"the provider answered {status}: token rejected or not permitted" + needed
        )
    if status != 200:
        raise ProviderError(f"the provider answered with status {status}")
    try:
        body = response.json()
    except ValueError as error:
        raise ProviderError("the provider answered with invalid JSON") from error
    if not isinstance(body, Mapping) or "data" not in body:
        raise ProviderError("the provider answer has no data member")
    return body["data"]


def _as_list(data: _Json) -> list[_Json]:
    if not isinstance(data, list):
        raise ProviderError("the provider answer has an unexpected shape")
    return data


def _to_server(item: Mapping[str, _Json]) -> Server:
    identifier = str(item["vmid"])
    return Server(
        server_identifier=identifier,
        name=str(item.get("name") or identifier),
        state=str(item.get("status") or _UNKNOWN_STATE),
        size=_size_text(item.get("maxcpu"), item.get("maxmem")),
        addresses=(),
        managed=False,
    )


def _size_text(cpu: _Json, memory_bytes: _Json) -> str:
    parts: list[str] = []
    if isinstance(cpu, int | float):
        parts.append(f"{cpu:g} vCPU")
    if isinstance(memory_bytes, int | float):
        parts.append(f"{int(memory_bytes) // _BYTES_PER_MIB} MiB")
    return ", ".join(parts) or "unknown"
