"""ProviderPort implementation for Proxmox VE, read-only.

Authentication is by Proxmox VE API token only in this slice: the secret of
the credentials is the full token string ``USER@REALM!TOKENID=SECRET`` and is
sent as ``Authorization: PVEAPIToken=<secret>``. Ticket login
(``POST /access/ticket``) is out of scope. Only HTTP GET is ever issued.
"""

import ssl
from collections.abc import Mapping
from typing import Any

import httpx

from iac_node_forge.application.errors import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderUnreachableError,
    ServerNotFoundError,
    UnknownOptionKindError,
)
from iac_node_forge.domain.access_result import AccessResult
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_option import ServerOption

API_PREFIX = "/api2/json"
OPTION_KIND_NODE = "node"
OPTION_KIND_STORAGE = "storage"
CAPABILITY_LIST_SERVERS = "list_servers"
CAPABILITY_LIST_SERVER_OPTIONS = "list_server_options"
CAPABILITY_INSPECT_SERVER = "inspect_server"
DEFAULT_TIMEOUT_SECONDS = 10.0
_BYTES_PER_MIB = 1024 * 1024
_UNKNOWN_STATE = "unknown"

# JSON from the provider is untyped by nature; it is narrowed in this module.
type _Json = Any


class ProxmoxProvider:
    """Reads nodes, storage and virtual machines from a Proxmox VE API.

    ``credentials`` is injected at construction because the ProviderPort
    methods other than ``validate_access`` receive only a configuration, and a
    configuration never holds a secret. The address of every call comes from
    the method arguments, so one adapter serves any configuration.

    ``verify`` is ``True`` (system CAs), a CA bundle path, or an explicit
    ``False`` for a self-signed certificate; it is never disabled silently.
    """

    def __init__(
        self,
        credentials: Credentials,
        verify: bool | str = True,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._credentials = credentials
        self._verify = verify
        self._timeout = timeout

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
        """Return the read-only capabilities of this adapter."""
        return frozenset(
            Capability(name)
            for name in (
                CAPABILITY_LIST_SERVERS,
                CAPABILITY_LIST_SERVER_OPTIONS,
                CAPABILITY_INSPECT_SERVER,
            )
        )

    def list_server_options(
        self, configuration: ProviderConfiguration, option_kind: str
    ) -> tuple[ServerOption, ...]:
        """Return ``node`` or ``storage`` options.

        A storage option value is ``<node>/<storage>``, because storage is
        read per node and a name such as ``local`` exists on every node.

        Raises UnknownOptionKindError for any other kind.
        """
        if option_kind == OPTION_KIND_NODE:
            return tuple(
                ServerOption(OPTION_KIND_NODE, name)
                for name in self._node_names(configuration)
            )
        if option_kind == OPTION_KIND_STORAGE:
            return self._storage_options(configuration)
        raise UnknownOptionKindError(
            f"Proxmox VE offers no options of kind {option_kind!r}"
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

    def _get_data(self, address: str, path: str) -> _Json:
        return self._get(address, path, self._credentials)

    def _get(self, address: str, path: str, credentials: Credentials) -> _Json:
        url = _base_url(address) + API_PREFIX + path
        headers = {"Authorization": f"PVEAPIToken={credentials.secret}"}
        try:
            with httpx.Client(
                verify=_tls_verify(self._verify), timeout=self._timeout
            ) as client:
                response = client.get(url, headers=headers)
        except httpx.TimeoutException as error:
            raise ProviderUnreachableError(
                f"the provider did not answer within {self._timeout} seconds"
            ) from error
        except httpx.TransportError as error:
            raise ProviderUnreachableError(
                f"the provider could not be reached ({type(error).__name__}); "
                "check the address and the TLS certificate"
            ) from error
        return _payload(response)


def _payload(response: httpx.Response) -> _Json:
    status = response.status_code
    if status in (401, 403):
        raise ProviderAuthenticationError(
            f"the provider answered {status}: token rejected or not permitted"
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


def _base_url(address: str) -> str:
    text = address.strip().rstrip("/")
    return text if "://" in text else f"https://{text}"


def _tls_verify(verify: bool | str) -> ssl.SSLContext | bool:
    if isinstance(verify, str):
        return ssl.create_default_context(cafile=verify)
    return verify


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
