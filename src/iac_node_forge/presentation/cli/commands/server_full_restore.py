"""``server full-restore``: UC-019 and UC-006 at the command line.

The word ``server`` is shared with the read command
``server <configuration> <identifier>``; ``is_full_restore_request`` tells them
apart before parsing. The verb is ``full-restore`` because ``server restore``
copies files back to a server (ADR-0009).
"""

import argparse
from collections.abc import Sequence

from iac_node_forge.application.backup_dto import (
    RestoreOutcome,
    RestorePreview,
    RestoreResult,
)
from iac_node_forge.application.dto import OperationSummary
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands._follow import (
    add_wait_options,
    follow_and_report,
)
from iac_node_forge.presentation.cli.commands._full_backup import (
    PROGRAM,
    build_restore_server,
    kind_note,
    write_backup,
)
from iac_node_forge.presentation.cli.commands._transfer import is_verb_request
from iac_node_forge.presentation.cli.commands._typed_confirmation import (
    typed_confirmation,
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

FULL_RESTORE = "full-restore"

_NOT_MANAGED_NOTICE = "This server is not managed by this client."
_EPILOG = """\
A restore cannot be undone. At Proxmox VE the guest, its disks and its
configuration are overwritten by the archive; what the overwrite leaves
untouched is not documented. At Contabo the instance is rolled back to the
snapshot and every newer snapshot is deleted; the preview lists them. The
command states this first and asks you to type the server identifier to confirm.
A server this client does not manage needs the identifier typed a second time.

A running Proxmox VE guest is refused and is never stopped for you: shut it down
first. Name the backup by its identifier or its short alias ('server
full-backups' lists them). Nothing is copied to this computer.

Without a terminal on standard input nothing is asked: --yes is required (the
identifier on the command line is then the identifier typed) and, for a server
not managed by this client, --confirm-unmanaged <identifier>; without them no
request is sent and the exit code is 1. At a terminal the identifier is always
asked for, whatever --yes says.
"""


def is_full_restore_request(argv: Sequence[str]) -> bool:
    """Whether ``argv`` is ``server full-restore ...``, not ``server <cfg> <id>``."""
    return is_verb_request(argv, FULL_RESTORE)


def build_parser() -> argparse.ArgumentParser:
    """Build the parser of the full-restore command."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Restore a whole server from a backup and follow the operation.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verbs = parser.add_subparsers(dest="verb", metavar="action", required=True)
    sub = verbs.add_parser(
        FULL_RESTORE,
        help="restore a whole server from a backup (it cannot be undone)",
        description="State what the restore replaces, ask for confirmation, send "
        "one restore request and follow the operation.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub.add_argument("configuration", help="identifier or display name")
    sub.add_argument("identifier", help="server identifier at the provider")
    sub.add_argument("backup_id", metavar="backup-id", help="identifier or alias")
    sub.add_argument(
        "--yes",
        action="store_true",
        help="confirm the restore without a terminal; the identifier given is "
        "the identifier typed",
    )
    sub.add_argument(
        "--confirm-unmanaged",
        metavar="IDENTIFIER",
        help="the identifier of a server not managed by this client, typed "
        "again as the extra confirmation without asking",
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
    """Show the preview, confirm, request one restore and follow it."""
    resolver = build_resolver(services)
    restore = build_restore_server(services, resolver)
    preview = restore.preview_restore(
        arguments.configuration, arguments.identifier, arguments.backup_id
    )
    _write_preview(services, preview)
    if preview.outcome is not RestoreOutcome.READY:
        return _not_sent(services, preview.outcome, preview.reason, preview, False)
    confirmation = typed_confirmation(
        services,
        "restore",
        arguments.identifier,
        preview.managed,
        arguments.yes,
        arguments.confirm_unmanaged,
    )
    result = restore.request_restore(
        arguments.configuration,
        arguments.identifier,
        arguments.backup_id,
        confirmation,
    )
    operation = result.operation
    if operation is None:
        return _not_sent(
            services, result.outcome, result.reason, preview, services.is_interactive
        )
    if result.outcome is RestoreOutcome.REJECTED:
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
        f"Requested: restore of server {preview.server_identifier!r} at "
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


def _write_preview(services: CliServices, preview: RestorePreview) -> None:
    """State what is known and, when it is offered, what the restore replaces."""
    out = services.stdout
    if preview.server_state is None:
        return
    label = words_for(preview.provider_kind).label
    name = f" ({preview.server_name})" if preview.server_name else ""
    write_line(
        out,
        f"About to restore server {preview.server_identifier!r}{name} at provider "
        f"{preview.provider_name!r}.",
    )
    write_detail_line(out, "Provider", f"{preview.provider_name} ({label})")
    write_detail_line(out, "Server", f"{preview.server_identifier}{name}")
    write_detail_line(out, "State", preview.server_state)
    write_detail_line(out, "Managed", "yes" if preview.managed else "no")
    if preview.backup is not None:
        write_line(out, "Restore from:")
        write_backup(out, preview.backup)
    if preview.consequence:
        write_detail_line(out, "Consequence", preview.consequence)
    if preview.deleted_backups:
        write_line(out, "The restore also deletes these backups:")
        for deleted in preview.deleted_backups:
            write_backup(out, deleted)
    note = kind_note(preview.backup.kind if preview.backup else None)
    if note is not None and preview.outcome is RestoreOutcome.READY:
        write_line(out, note)
    if not preview.managed and preview.outcome is RestoreOutcome.READY:
        write_line(out, _NOT_MANAGED_NOTICE)


def _not_sent(
    services: CliServices,
    outcome: RestoreOutcome,
    reason: str,
    preview: RestorePreview,
    interactive: bool,
) -> int:
    """Report an outcome in which no request was sent; the exit code is 1."""
    if outcome is RestoreOutcome.NOT_CONFIRMED:
        write_not_confirmed(
            services, "restore", preview.server_identifier, preview.managed, interactive
        )
        return exit_codes.FAILURE
    err = services.stderr
    write_line(
        err, f"error: server {preview.server_identifier!r} was not restored: {reason}"
    )
    write_line(err, f"Hint: {_hint(outcome, preview)} No request was sent.")
    return exit_codes.FAILURE


def _hint(outcome: RestoreOutcome, preview: RestorePreview) -> str:
    configuration = preview.configuration_id
    identifier = preview.server_identifier
    if outcome is RestoreOutcome.UNSUPPORTED:
        return (
            f"'iac-node-forge capabilities {configuration}' shows what the "
            "provider supports."
        )
    if outcome is RestoreOutcome.NOT_FOUND:
        return (
            "list the servers with 'iac-node-forge servers --configuration "
            f"{configuration}'."
        )
    if outcome is RestoreOutcome.BACKUP_NOT_FOUND:
        return (
            f"list the backups with 'iac-node-forge server full-backups "
            f"{configuration} {identifier}'."
        )
    if outcome is RestoreOutcome.BACKUP_MISMATCH:
        return "choose a backup of this server and its kind of guest."
    if outcome is RestoreOutcome.RUNNING_REFUSED:
        return (
            "shut it down first with 'iac-node-forge server shutdown "
            f"{configuration} {identifier}'."
        )
    if outcome is RestoreOutcome.NOT_AVAILABLE:
        return "the server must be available at the provider."
    return f"type {identifier!r} exactly."


def _rejected(
    services: CliServices, result: RestoreResult, operation: OperationSummary
) -> int:
    write_operation(services.stdout, operation)
    err = services.stderr
    write_line(
        err,
        f"error: the provider rejected the restore of server "
        f"{operation.server_identifier!r}: {result.reason}",
    )
    if result.state_after is not None:
        write_line(err, f"State left at the provider: server is {result.state_after}")
    write_line(
        err,
        "Hint: check that the credentials may restore servers; the request was "
        "not repeated.",
    )
    return exit_codes.FAILURE
