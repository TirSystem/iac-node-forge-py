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
    server_provision,
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
     provider rejected the request, or the operation ended failed or unknown;
     for 'server define' also: the description is not valid (it is not saved);
     for 'server provision' also: the description is invalid or unsupported, a
     server of that name exists or an earlier request for it is not finished
  2  usage error

power commands (they change the state of a server):
  server start|shutdown|stop|restart <configuration> <identifier>
      asks for confirmation, sends one request and follows the operation;
      'server <configuration> <identifier>' still shows a server;
      see 'iac-node-forge server start --help'
  operation <operation-id>
      shows a recorded operation (--follow follows one that is running)

provisioning (creating a server is a write and may commit the account):
  server define <configuration> <name> --image IMAGE [--key NAME] [--hint TEXT]
                [--set NAME=VALUE]
      checks and saves a named desired configuration; nothing invalid is saved
  server provision <configuration> <name> [--name SERVER_NAME] [--yes]
      shows what will be created and its cost, asks, sends one create request
      and follows the operation; the server is then recorded as managed
  server definitions <configuration> [--check]
      lists the desired configurations
      see 'iac-node-forge server provision --help'
"""


def build_parser() -> argparse.ArgumentParser:
    """Build the parser of every command."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Command-line client for Proxmox VE and Contabo servers: read "
        "servers and options, define and provision servers, and start, shut down, "
        "stop or restart a server.",
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
    group = _server_group_parser(words)
    parser = group or build_parser()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            arguments = parser.parse_args(words[1:] if group else words)
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


def _server_group_parser(words: Sequence[str]) -> argparse.ArgumentParser | None:
    """Return the parser of ``server <verb> ...`` when ``words`` is such a command."""
    if server_power.is_power_request(words):
        return server_power.build_parser()
    if server_provision.is_provision_request(words):
        return server_provision.build_parser()
    return None


def _kind_of(services: CliServices, name: str) -> ProviderKind | None:
    """Return the provider kind of the named configuration, if it can be found."""
    try:
        return find_configuration(services.configurations, name).provider_kind
    except (UseCaseError, ConfigurationFormatError):
        return None
