"""Wiring of the removal use case, shared by the commands that use it."""

from iac_node_forge.application.use_cases import RemoveServer
from iac_node_forge.presentation.cli.provider_resolver import CliProviderResolver
from iac_node_forge.presentation.cli.services import CliServices


def build_remove_server(
    services: CliServices, resolver: CliProviderResolver
) -> RemoveServer:
    """Wire RemoveServer for a command."""
    return RemoveServer(
        resolver, services.configurations, services.operations, services.clock
    )
