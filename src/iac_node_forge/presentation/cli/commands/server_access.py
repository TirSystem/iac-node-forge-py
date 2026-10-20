"""``server access``: record how to reach a server over SSH (UC-016)."""

import argparse
from collections.abc import Sequence

from iac_node_forge.application.transfer_dto import AccessOutcome
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands._transfer import (
    PROGRAM,
    build_define_access,
    is_verb_request,
)
from iac_node_forge.presentation.cli.operation_output import write_detail_line
from iac_node_forge.presentation.cli.output import NO_VALUE, write_line
from iac_node_forge.presentation.cli.services import CliServices

ACCESS = "access"
_EPILOG = """\
Records the host, port, user and an identity hint of a server. No password,
passphrase or key is ever stored: ssh uses your own key, agent and known_hosts.
The identity hint is a note or a key file path; key material is refused. Without
--host the address the provider reports is used at each transfer.
"""


def is_access_request(argv: Sequence[str]) -> bool:
    """Whether ``argv`` is ``server access ...`` and not ``server <cfg> <id>``."""
    return is_verb_request(argv, ACCESS)


def build_parser() -> argparse.ArgumentParser:
    """Build the parser of the access command."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Record how to reach a server over SSH.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verbs = parser.add_subparsers(dest="verb", metavar="action", required=True)
    sub = verbs.add_parser(
        ACCESS,
        help="record the host, port and user used to reach a server",
        description="Record how to reach a server over SSH.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub.add_argument("configuration", help="identifier or display name")
    sub.add_argument("identifier", help="server identifier at the provider")
    sub.add_argument("--user", required=True, help="user name to log in with")
    sub.add_argument("--host", help="name or address; default: the provider's")
    sub.add_argument("--port", type=int, help="SSH port (default 22)")
    sub.add_argument("--identity", help="note or key file path (never key material)")
    sub.set_defaults(handler=run)
    return parser


def run(arguments: argparse.Namespace, services: CliServices) -> int:
    """Record the access and print what was kept."""
    view = build_define_access(services).define_server_access(
        arguments.configuration,
        arguments.identifier,
        arguments.host,
        arguments.port,
        arguments.user,
        arguments.identity,
    )
    if view.outcome is not AccessOutcome.SAVED:
        write_line(
            services.stderr,
            f"error: the access of server {arguments.identifier!r} was not "
            f"saved: {view.reason}",
        )
        return exit_codes.FAILURE
    out = services.stdout
    write_line(out, f"Saved the access of server {view.server_identifier!r}.")
    write_detail_line(out, "Host", view.host or "(the provider's address)")
    write_detail_line(out, "Port", str(view.port))
    write_detail_line(out, "User", view.user or NO_VALUE)
    write_detail_line(out, "Identity", view.identity_hint or NO_VALUE)
    return exit_codes.SUCCESS
