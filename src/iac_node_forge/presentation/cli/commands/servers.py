"""``servers``: list the servers of the configured providers."""

import argparse

from iac_node_forge.application.use_cases import ListServers
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands import Subparsers
from iac_node_forge.presentation.cli.output import NO_VALUE, format_table, write_line
from iac_node_forge.presentation.cli.provider_resolver import build_resolver
from iac_node_forge.presentation.cli.services import CliServices

MANAGED = "managed"
NOT_MANAGED = "not managed"

_HEADERS = ["Provider", "Id", "Name", "State", "Size", "Addresses", "Managed"]


def add_parser(subparsers: Subparsers) -> None:
    """Register ``servers``."""
    parser = subparsers.add_parser(
        "servers",
        help="list servers across the configured providers",
    )
    parser.add_argument(
        "--configuration", help="list only this configuration (identifier or name)"
    )
    parser.set_defaults(handler=run)


def build_use_case(services: CliServices) -> ListServers:
    """Wire ListServers for a command."""
    return ListServers(build_resolver(services), services.configurations)


def run(arguments: argparse.Namespace, services: CliServices) -> int:
    """Print the servers; report each failed provider and exit with 1."""
    listing = build_use_case(services).list_servers(arguments.configuration)
    if listing.servers:
        rows = [
            [
                server.provider_name,
                server.server_identifier,
                server.name,
                server.state,
                server.size,
                ", ".join(server.addresses) or NO_VALUE,
                MANAGED if server.managed else NOT_MANAGED,
            ]
            for server in listing.servers
        ]
        for line in format_table(_HEADERS, rows):
            write_line(services.stdout, line)
    elif not listing.failures:
        write_line(services.stdout, "No servers found.")
    for failure in listing.failures:
        write_line(
            services.stderr,
            f"error: provider {failure.provider_name!r} could not be read "
            f"({failure.category.value}): {failure.reason}",
        )
    return exit_codes.FAILURE if listing.failures else exit_codes.SUCCESS
