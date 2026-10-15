"""One ProviderPort implementation with the data it serves, for contract tests."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from iac_node_forge.application.ports import ProviderPort, ServerPowerPort
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
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
