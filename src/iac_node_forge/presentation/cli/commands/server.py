"""``server``: show the details of one server."""

import argparse

from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands import Subparsers
from iac_node_forge.presentation.cli.commands.servers import (
    MANAGED,
    NOT_MANAGED,
    build_use_case,
)
from iac_node_forge.presentation.cli.output import NO_VALUE, write_line
from iac_node_forge.presentation.cli.services import CliServices


def add_parser(subparsers: Subparsers) -> None:
    """Register ``server``."""
    parser = subparsers.add_parser(
        "server",
        help="show the details of one server; start, shutdown, stop, restart it",
        description="Show the details of one server. The same word starts the "
        "power commands: 'server start|shutdown|stop|restart <configuration> "
        "<identifier>' (see 'iac-node-forge server start --help').",
    )
    parser.add_argument("configuration", help="identifier or display name")
    parser.add_argument("identifier", help="server identifier at the provider")
    parser.set_defaults(handler=run)


def run(arguments: argparse.Namespace, services: CliServices) -> int:
    """Print the details of one server."""
    server = build_use_case(services).inspect_server(
        arguments.configuration, arguments.identifier
    )
    details = [
        ("Provider", server.provider_name),
        ("Identifier", server.server_identifier),
        ("Name", server.name),
        ("State", server.state),
        ("Size", server.size),
        ("Addresses", ", ".join(server.addresses) or NO_VALUE),
        ("Managed", MANAGED if server.managed else NOT_MANAGED),
    ]
    for label, value in details:
        write_line(services.stdout, f"{label + ':':<12}{value}")
    return exit_codes.SUCCESS
