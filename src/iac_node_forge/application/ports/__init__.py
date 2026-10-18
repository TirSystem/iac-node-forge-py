"""Ports: the interfaces the application defines for things outside it."""

from iac_node_forge.application.ports.configuration_store import ConfigurationStore
from iac_node_forge.application.ports.credential_prompt import CredentialPrompt
from iac_node_forge.application.ports.credential_store import CredentialStore
from iac_node_forge.application.ports.desired_configuration_store import (
    DesiredConfigurationStore,
)
from iac_node_forge.application.ports.operation_store import OperationStore
from iac_node_forge.application.ports.provider_port import ProviderPort
from iac_node_forge.application.ports.provider_resolver import ProviderResolver
from iac_node_forge.application.ports.provision_intent_store import (
    ProvisionIntentStore,
)
from iac_node_forge.application.ports.server_power_port import ServerPowerPort
from iac_node_forge.application.ports.server_provision_port import (
    ServerProvisionPort,
)
from iac_node_forge.application.ports.server_removal_port import (
    RemovalBlock,
    ServerRemovalPort,
)

__all__ = [
    "ConfigurationStore",
    "CredentialPrompt",
    "CredentialStore",
    "DesiredConfigurationStore",
    "OperationStore",
    "ProviderPort",
    "ProviderResolver",
    "ProvisionIntentStore",
    "RemovalBlock",
    "ServerPowerPort",
    "ServerProvisionPort",
    "ServerRemovalPort",
]
