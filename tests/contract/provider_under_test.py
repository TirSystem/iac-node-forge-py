"""One ProviderPort implementation with the data it serves, for contract tests."""

from collections.abc import Mapping
from dataclasses import dataclass

from iac_node_forge.application.ports import ProviderPort
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
    missing_identifier: str = "999"
    unknown_option_kind: str = "flavour"
