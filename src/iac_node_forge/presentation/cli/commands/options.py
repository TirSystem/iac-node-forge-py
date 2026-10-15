"""``options``: show the server options of one kind a provider offers."""

import argparse

from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands import Subparsers
from iac_node_forge.presentation.cli.commands.capabilities import build_use_case
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.provider_words import describe_option_kinds
from iac_node_forge.presentation.cli.services import CliServices


def add_parser(subparsers: Subparsers) -> None:
    """Register ``options``."""
    parser = subparsers.add_parser(
        "options",
        help="show the server options of one kind the provider offers",
        description=f"Option kinds by provider: {describe_option_kinds()}.",
    )
    parser.add_argument("configuration", help="identifier or display name")
    parser.add_argument("kind", help="option kind of the configuration's provider")
    parser.set_defaults(handler=run)


def run(arguments: argparse.Namespace, services: CliServices) -> int:
    """Print the options, one per line."""
    options = build_use_case(services).list_server_options(
        arguments.configuration, arguments.kind
    )
    if not options:
        write_line(services.stdout, f"No options of kind {arguments.kind!r}.")
        return exit_codes.SUCCESS
    write_line(services.stdout, f"Options of kind {arguments.kind!r}:")
    for option in options:
        write_line(services.stdout, f"  {option.value}")
    return exit_codes.SUCCESS
