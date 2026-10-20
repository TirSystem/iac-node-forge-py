"""``server backup``: copy server paths to this PC and verify them (UC-016)."""

import argparse
from collections.abc import Sequence

from iac_node_forge.application.transfer_dto import TransferOutcome
from iac_node_forge.presentation.cli.commands._transfer import (
    PROGRAM,
    add_accept_host_key,
    build_back_up,
    is_verb_request,
    preview_failure,
    write_preview,
    write_report,
)
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.services import CliServices

BACKUP = "backup"
_EPILOG = """\
A backup only reads on the server: nothing there is created, changed or
deleted. Each path is copied under the destination at its full path
(/etc/nginx becomes <to>/etc/nginx); links are copied as links. The copy is
compared with the source by checksum and is reported as done only when equal.
The destination must not exist or be empty and must have room for the data plus
a margin of 10 percent and 16 MiB. The server must be running, its access
recorded ('server access') and its host key in your known_hosts; for a new
server the command prints the fingerprint and the exact option to accept it.
A report is saved in the destination. --yes is accepted and not needed.
"""


def is_backup_request(argv: Sequence[str]) -> bool:
    """Whether ``argv`` is ``server backup ...`` and not ``server <cfg> <id>``."""
    return is_verb_request(argv, BACKUP)


def build_parser() -> argparse.ArgumentParser:
    """Build the parser of the backup command."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Copy files from a server to this computer and verify them.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verbs = parser.add_subparsers(dest="verb", metavar="action", required=True)
    sub = verbs.add_parser(
        BACKUP,
        help="copy server paths to a local directory and verify the copy",
        description="Copy files from a server to this computer and verify them.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub.add_argument("configuration", help="identifier or display name")
    sub.add_argument("identifier", help="server identifier at the provider")
    sub.add_argument("remote_paths", nargs="+", metavar="remote-path")
    sub.add_argument("--to", required=True, metavar="LOCAL_DIR", dest="destination")
    sub.add_argument("--yes", action="store_true", help="accepted; not needed")
    add_accept_host_key(sub)
    sub.set_defaults(handler=run)
    return parser


def run(arguments: argparse.Namespace, services: CliServices) -> int:
    """Show what will be copied, copy, verify and report."""
    backup = build_back_up(services)
    preview = backup.preview_backup(
        arguments.configuration,
        arguments.identifier,
        arguments.remote_paths,
        arguments.destination,
        arguments.accept_host_key,
    )
    if preview.outcome is not TransferOutcome.READY:
        return preview_failure(services, "backup", preview, arguments.configuration)
    write_line(
        services.stdout,
        f"About to copy {len(preview.paths)} path(s) from server "
        f"{preview.server_identifier!r}. Nothing on the server is changed.",
    )
    write_preview(services, preview)
    result = backup.request_backup(
        arguments.configuration,
        arguments.identifier,
        arguments.remote_paths,
        arguments.destination,
    )
    return write_report(services, result)
