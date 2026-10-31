"""``server full-backup``: UC-018 and UC-006 at the command line.

The word ``server`` is shared with the read command
``server <configuration> <identifier>``; ``is_full_backup_request`` tells them
apart before parsing, as the other server commands do. The verb is
``full-backup`` because ``server backup`` copies files (ADR-0009).
"""

import argparse
from collections.abc import Sequence

from iac_node_forge.application.backup_dto import (
    BackupOutcome,
    BackupPreview,
    BackupResult,
)
from iac_node_forge.application.dto import Confirmation, OperationSummary
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands._follow import (
    add_wait_options,
    follow_and_report,
)
from iac_node_forge.presentation.cli.commands._full_backup import (
    PROGRAM,
    build_back_up_server,
    kind_note,
)
from iac_node_forge.presentation.cli.commands._transfer import is_verb_request
from iac_node_forge.presentation.cli.commands._typed_confirmation import (
    write_not_confirmed,
)
from iac_node_forge.presentation.cli.operation_output import (
    write_detail_line,
    write_operation,
)
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.provider_resolver import build_resolver
from iac_node_forge.presentation.cli.provider_words import words_for
from iac_node_forge.presentation.cli.services import CliServices

FULL_BACKUP = "full-backup"

_YES_ANSWERS = frozenset({"y", "yes"})
_EPILOG = """\
A backup of the whole server changes nothing on the server and deletes nothing:
it only needs a plain yes (--yes without a terminal). At Proxmox VE it writes an
archive file to a storage that accepts backups (--storage names one; without it
the only such storage is used). At Contabo it makes a snapshot, which is not an
independent backup: the provider keeps it on the same disk system as the server,
limits how many there are and deletes it on a date it sets; its cost is not
stated. The command follows the operation, then tells where the backup is kept.
No copy is made to this computer.

Without a terminal on standard input nothing is asked: without --yes no request
is sent and the exit code is 1.
"""


def is_full_backup_request(argv: Sequence[str]) -> bool:
    """Whether ``argv`` is ``server full-backup ...`` and not ``server <cfg> <id>``."""
    return is_verb_request(argv, FULL_BACKUP)


def build_parser() -> argparse.ArgumentParser:
    """Build the parser of the full-backup command."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Back up a whole server at its provider and follow the operation.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verbs = parser.add_subparsers(dest="verb", metavar="action", required=True)
    sub = verbs.add_parser(
        FULL_BACKUP,
        help="back up a whole server at its provider (an archive or a snapshot)",
        description="State what the backup is and where it is kept, ask for a "
        "yes, send one backup request and follow the operation.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub.add_argument("configuration", help="identifier or display name")
    sub.add_argument("identifier", help="server identifier at the provider")
    sub.add_argument(
        "--storage",
        metavar="STORAGE",
        help="the storage that keeps the archive (Proxmox VE); Contabo has none",
    )
    sub.add_argument(
        "--yes", action="store_true", help="confirm the backup without a terminal"
    )
    sub.add_argument(
        "--no-wait",
        action="store_true",
        help="print the operation and exit; follow it with "
        "'iac-node-forge operation <id> --follow'",
    )
    add_wait_options(sub)
    sub.set_defaults(handler=run)
    return parser


def run(arguments: argparse.Namespace, services: CliServices) -> int:
    """Show the preview, confirm, request one backup and follow it."""
    resolver = build_resolver(services)
    backup = build_back_up_server(services, resolver)
    preview = backup.preview_backup(
        arguments.configuration, arguments.identifier, arguments.storage
    )
    _write_preview(services, preview)
    if preview.outcome is not BackupOutcome.READY:
        return _not_sent(services, preview.outcome, preview.reason, preview, False)
    confirmation = _confirmation(services, arguments, preview)
    result = backup.request_backup(
        arguments.configuration, arguments.identifier, arguments.storage, confirmation
    )
    operation = result.operation
    if operation is None:
        return _not_sent(
            services, result.outcome, result.reason, preview, services.is_interactive
        )
    if result.outcome is BackupOutcome.REJECTED:
        return _rejected(services, result, operation)
    if arguments.no_wait:
        write_operation(services.stdout, operation)
        write_line(
            services.stdout,
            "Follow it with: "
            f"iac-node-forge operation {operation.operation_id} --follow",
        )
        return exit_codes.SUCCESS
    write_line(
        services.stdout,
        f"Requested: backup of server {preview.server_identifier!r} at "
        f"{preview.provider_name!r}; following the operation for at most "
        f"{arguments.timeout:g} seconds.",
    )
    return follow_and_report(
        services,
        resolver,
        operation.operation_id,
        arguments.timeout,
        arguments.poll_interval,
    )


def _write_preview(services: CliServices, preview: BackupPreview) -> None:
    """State what is known of the server and, when it is offered, the backup."""
    out = services.stdout
    if preview.server_state is None:
        return
    label = words_for(preview.provider_kind).label
    name = f" ({preview.server_name})" if preview.server_name else ""
    write_line(
        out,
        f"About to back up server {preview.server_identifier!r}{name} at provider "
        f"{preview.provider_name!r}.",
    )
    write_detail_line(out, "Provider", f"{preview.provider_name} ({label})")
    write_detail_line(out, "Server", f"{preview.server_identifier}{name}")
    write_detail_line(out, "State", preview.server_state)
    if preview.kind is not None:
        write_detail_line(out, "Backup", preview.kind.value)
    if preview.location:
        write_detail_line(out, "Goes to", preview.location)
    if preview.consequence:
        write_detail_line(out, "Consequence", preview.consequence)
    note = kind_note(preview.kind)
    if note is not None:
        write_line(out, note)


def _confirmation(
    services: CliServices, arguments: argparse.Namespace, preview: BackupPreview
) -> Confirmation:
    """A plain yes: ``--yes``, or the answer at a terminal."""
    if arguments.yes or not services.is_interactive:
        return Confirmation(given=arguments.yes)
    answer = services.read_line("Do you want to back up this server? [yes/no]: ")
    return Confirmation(given=answer.strip().casefold() in _YES_ANSWERS)


def _not_sent(
    services: CliServices,
    outcome: BackupOutcome,
    reason: str,
    preview: BackupPreview,
    interactive: bool,
) -> int:
    """Report an outcome in which no request was sent; the exit code is 1."""
    if outcome is BackupOutcome.NOT_CONFIRMED:
        write_not_confirmed(
            services, "backup", preview.server_identifier, True, interactive
        )
        return exit_codes.FAILURE
    err = services.stderr
    write_line(
        err, f"error: server {preview.server_identifier!r} was not backed up: {reason}"
    )
    write_line(err, f"Hint: {_hint(outcome, preview)} No request was sent.")
    return exit_codes.FAILURE


def _hint(outcome: BackupOutcome, preview: BackupPreview) -> str:
    configuration = preview.configuration_id
    if outcome is BackupOutcome.UNSUPPORTED:
        return (
            f"'iac-node-forge capabilities {configuration}' shows what the "
            "provider supports."
        )
    if outcome is BackupOutcome.NOT_FOUND:
        return (
            "list the servers with 'iac-node-forge servers --configuration "
            f"{configuration}'."
        )
    if outcome is BackupOutcome.STORAGE_REQUIRED:
        return "repeat the command with --storage and one of the storages named."
    if outcome is BackupOutcome.NO_BACKUP_STORAGE:
        return (
            "give a storage of the node the content type 'backup' at the "
            "provider first."
        )
    if outcome is BackupOutcome.STORAGE_UNSUITABLE:
        return (
            "name a storage that accepts backups with --storage (a provider "
            "without storages takes none)."
        )
    return "the server must be available at the provider."


def _rejected(
    services: CliServices, result: BackupResult, operation: OperationSummary
) -> int:
    write_operation(services.stdout, operation)
    err = services.stderr
    write_line(
        err,
        f"error: the provider rejected the backup of server "
        f"{operation.server_identifier!r}: {result.reason}",
    )
    if result.state_left:
        write_line(err, f"State left at the provider: {result.state_left}")
    write_line(
        err,
        "Hint: check that the credentials may back up servers; the request was "
        "not repeated.",
    )
    return exit_codes.FAILURE
