"""``server full-backups``: list the backups of a server (UC-019).

``is_full_backups_request`` tells the command from ``server <cfg> <id>`` before
parsing, as the other server commands do.
"""

import argparse
from collections.abc import Sequence

from iac_node_forge.application.backup_dto import BackupListing, BackupOutcome
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands._full_backup import (
    PROGRAM,
    build_list_backups,
    kind_note,
    write_backup,
)
from iac_node_forge.presentation.cli.commands._transfer import is_verb_request
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.provider_resolver import build_resolver
from iac_node_forge.presentation.cli.services import CliServices

FULL_BACKUPS = "full-backups"

_EPILOG = """\
The list is what the provider holds for the server, oldest first, so it also
shows backups made by other tools. A backup made by this client names the
operation that made it. A backup this client recorded that the provider no
longer lists is shown apart and its record is kept. Name a backup by its
identifier or by its short alias (8 characters) in 'server full-restore'.
Nothing is written.
"""


def is_full_backups_request(argv: Sequence[str]) -> bool:
    """Whether ``argv`` is ``server full-backups ...``, not ``server <cfg> <id>``."""
    return is_verb_request(argv, FULL_BACKUPS)


def build_parser() -> argparse.ArgumentParser:
    """Build the parser of the full-backups command."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="List the backups of a server at its provider.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verbs = parser.add_subparsers(dest="verb", metavar="action", required=True)
    sub = verbs.add_parser(
        FULL_BACKUPS,
        help="list the backups of a server",
        description="List the backups the provider holds for a server.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub.add_argument("configuration", help="identifier or display name")
    sub.add_argument("identifier", help="server identifier at the provider")
    sub.set_defaults(handler=run)
    return parser


def run(arguments: argparse.Namespace, services: CliServices) -> int:
    """Print the backups of the server."""
    listing = build_list_backups(services, build_resolver(services)).list_backups(
        arguments.configuration, arguments.identifier
    )
    if listing.outcome is not BackupOutcome.LISTED:
        return _not_listed(services, listing)
    _write_listing(services, listing)
    return exit_codes.SUCCESS


def _write_listing(services: CliServices, listing: BackupListing) -> None:
    out = services.stdout
    kind = f" ({listing.kind.value})" if listing.kind is not None else ""
    server = (
        f"server {listing.server_identifier!r} at provider {listing.provider_name!r}"
    )
    if listing.backups:
        write_line(out, f"Backups of {server}{kind}:")
        for backup in listing.backups:
            write_backup(out, backup)
    else:
        write_line(out, f"No backups are listed for {server}.")
    if listing.gone_from_provider:
        write_line(out, "Recorded by this client but no longer listed by the provider:")
        for backup in listing.gone_from_provider:
            write_backup(out, backup)
    note = kind_note(listing.kind)
    if note is not None and listing.backups:
        write_line(out, note)


def _not_listed(services: CliServices, listing: BackupListing) -> int:
    err = services.stderr
    write_line(err, f"error: the backups were not listed: {listing.reason}")
    if listing.outcome is BackupOutcome.UNSUPPORTED:
        write_line(
            err,
            f"Hint: 'iac-node-forge capabilities {listing.configuration_id}' "
            "shows what the provider supports.",
        )
    else:
        write_line(
            err,
            "Hint: list the servers with 'iac-node-forge servers "
            f"--configuration {listing.configuration_id}'.",
        )
    return exit_codes.FAILURE
