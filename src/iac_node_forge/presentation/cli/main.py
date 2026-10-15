"""Entry point of the command-line client: parse, run one command, map failures."""

import argparse
import os
import sys
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout

from iac_node_forge.application.errors import ConfigurationFormatError, UseCaseError
from iac_node_forge.application.use_cases import find_configuration
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands import (
    capabilities,
    operation,
    options,
    provider_add,
    server,
    server_power,
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
  1  a failure you can act on: provider unreachable or rejected the
     credentials (Proxmox VE: API token; Contabo: client ID, client secret,
     API user and API password), configuration or server not found, unknown
     option kind for the provider, nothing configured, a provider failed in
     'servers'; for a power command also: the action is unsupported, the server
     is already in the state, the confirmation is missing or refused, the
     provider rejected the request, or the operation ended failed or unknown
  2  usage error

power commands (they change the state of a server):
  server start|shutdown|stop|restart <configuration> <identifier>
      asks for confirmation, sends one request and follows the operation;
      'server <configuration> <identifier>' still shows a server;
      see 'iac-node-forge server start --help'
  operation <operation-id>
      shows a recorded operation (--follow follows one that is running)
"""


def build_parser() -> argparse.ArgumentParser:
    """Build the parser of every command."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Command-line client for Proxmox VE and Contabo servers: read "
        "servers and options, and start, shut down, stop or restart a server.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", metavar="command")
    provider = commands.add_parser("provider", help="manage provider configurations")
    provider_commands = provider.add_subparsers(
        dest="provider_command", metavar="action", required=True
    )
    provider_add.add_parser(provider_commands)
    for module in (capabilities, options, servers, server, operation):
        module.add_parser(commands)
    return parser


def main(argv: Sequence[str] | None = None, services: CliServices | None = None) -> int:
    """Run the client and return its exit code.

    ``services`` is injected by tests; when it is None the composition root
    builds the real ones from the environment.
    """
    stdout = services.stdout if services else sys.stdout
    stderr = services.stderr if services else sys.stderr
    words = list(sys.argv[1:] if argv is None else argv)
    is_power = server_power.is_power_request(words)
    parser = server_power.build_parser() if is_power else build_parser()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            arguments = parser.parse_args(words[1:] if is_power else words)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else exit_codes.USAGE
    handler = getattr(arguments, "handler", None)
    if handler is None:
        with redirect_stdout(stderr):
            parser.print_help()
        return exit_codes.USAGE
    active = services
    try:
        active = active or build_services(os.environ, stdout, stderr)
        return int(handler(arguments, active))
    except HANDLED_ERRORS as error:
        name = getattr(arguments, "configuration", None)
        kind = _kind_of(active, name) if active and name else None
        write_line(stderr, describe(error, name, kind))
    except KeyboardInterrupt:
        write_line(stderr, "error: interrupted")
    return exit_codes.FAILURE


def _kind_of(services: CliServices, name: str) -> ProviderKind | None:
    """Return the provider kind of the named configuration, if it can be found."""
    try:
        return find_configuration(services.configurations, name).provider_kind
    except (UseCaseError, ConfigurationFormatError):
        return None
