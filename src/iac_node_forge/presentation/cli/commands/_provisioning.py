"""Wiring of the provisioning use cases, shared by the commands that use them."""

from iac_node_forge.application.use_cases import (
    DefineDesiredConfiguration,
    DesiredConfigurationValidator,
    ProvisionServer,
)
from iac_node_forge.presentation.cli.provider_resolver import CliProviderResolver
from iac_node_forge.presentation.cli.services import CliServices


def build_define(
    services: CliServices, resolver: CliProviderResolver
) -> DefineDesiredConfiguration:
    """Wire DefineDesiredConfiguration for a command."""
    return DefineDesiredConfiguration(
        DesiredConfigurationValidator(resolver),
        services.configurations,
        services.desired_configurations,
    )


def build_provision_server(
    services: CliServices, resolver: CliProviderResolver
) -> ProvisionServer:
    """Wire ProvisionServer for a command."""
    return ProvisionServer(
        resolver,
        services.configurations,
        services.desired_configurations,
        services.provision_intents,
        services.operations,
        DesiredConfigurationValidator(resolver),
        services.clock,
    )
