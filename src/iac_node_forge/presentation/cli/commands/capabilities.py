"""``capabilities``: show what a configured provider supports."""

import argparse

from iac_node_forge.application.use_cases import DiscoverCapabilities
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands import Subparsers
from iac_node_forge.presentation.cli.output import format_table, write_line
from iac_node_forge.presentation.cli.provider_resolver import build_resolver
from iac_node_forge.presentation.cli.services import CliServices


def add_parser(subparsers: Subparsers) -> None:
    """Register ``capabilities``."""
    parser = subparsers.add_parser(
        "capabilities",
        help="show the actions a configured provider supports",
    )
    parser.add_argument("configuration", help="identifier or display name")
    parser.set_defaults(handler=run)


def build_use_case(services: CliServices) -> DiscoverCapabilities:
    """Wire DiscoverCapabilities for a command."""
    return DiscoverCapabilities(build_resolver(services), services.configurations)


def run(arguments: argparse.Namespace, services: CliServices) -> int:
    """Print every known action as supported or unsupported."""
    report = build_use_case(services).discover_capabilities(arguments.configuration)
    write_line(
        services.stdout,
        f"Capabilities of {report.display_name!r} ({report.provider_kind.value}):",
    )
    rows = [
        [entry.name, "supported" if entry.supported else "unsupported"]
        for entry in report.entries
    ]
    for line in format_table(["Action", "Status"], rows):
        write_line(services.stdout, line)
    return exit_codes.SUCCESS
