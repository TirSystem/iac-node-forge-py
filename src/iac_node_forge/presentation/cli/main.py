"""Entry point of the command-line client: parse, run one command, map failures."""

import argparse
import os
import sys
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout

from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands import (
    capabilities,
    options,
    provider_add,
    server,
    servers,
)
from iac_node_forge.presentation.cli.composition import build_services
from iac_node_forge.presentation.cli.failure_report import HANDLED_ERRORS, describe
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.services import CliServices

PROGRAM = "iac-node-forge"
_EPILOG = """\
environment variables:
  IAC_NODE_FORGE_CONFIG   path of the configuration file (JSON Lines); default is
                          the per-user configuration folder of the platform
  IAC_NODE_FORGE_VERIFY   TLS verification of provider connections: true
                          (default), false (for a self-signed certificate) or
                          the path of a CA bundle file

exit codes:
  0  success
  1  a failure you can act on: provider unreachable or rejected the token,
     configuration or server not found, unknown option kind, nothing configured
  2  usage error
"""


def build_parser() -> argparse.ArgumentParser:
    """Build the parser of every command."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Read-only command-line client for Proxmox VE servers.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", metavar="command")
    provider = commands.add_parser("provider", help="manage provider configurations")
    provider_commands = provider.add_subparsers(
        dest="provider_command", metavar="action", required=True
    )
    provider_add.add_parser(provider_commands)
    for module in (capabilities, options, servers, server):
        module.add_parser(commands)
    return parser


def main(argv: Sequence[str] | None = None, services: CliServices | None = None) -> int:
    """Run the client and return its exit code.

    ``services`` is injected by tests; when it is None the composition root
    builds the real ones from the environment.
    """
    stdout = services.stdout if services else sys.stdout
    stderr = services.stderr if services else sys.stderr
    parser = build_parser()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            arguments = parser.parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else exit_codes.USAGE
    handler = getattr(arguments, "handler", None)
    if handler is None:
        with redirect_stdout(stderr):
            parser.print_help()
        return exit_codes.USAGE
    try:
        active = services or build_services(os.environ, stdout, stderr)
        return int(handler(arguments, active))
    except HANDLED_ERRORS as error:
        write_line(stderr, describe(error, getattr(arguments, "configuration", None)))
    except KeyboardInterrupt:
        write_line(stderr, "error: interrupted")
    return exit_codes.FAILURE
