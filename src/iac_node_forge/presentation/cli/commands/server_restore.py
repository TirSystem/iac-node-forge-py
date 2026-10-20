"""``server restore``: copy local paths to a server and verify them (UC-017)."""

import argparse
from collections.abc import Sequence

from iac_node_forge.application.dto import Confirmation
from iac_node_forge.application.transfer_dto import TransferOutcome, TransferPreview
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands._transfer import (
    PROGRAM,
    add_accept_host_key,
    build_restore,
    is_verb_request,
    preview_failure,
    write_failure,
    write_preview,
    write_report,
)
from iac_node_forge.presentation.cli.operation_output import write_detail_line
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.services import CliServices

RESTORE = "restore"
NOT_KEPT = (
    "Anything replaced on the server is overwritten and its previous version is "
    "NOT kept."
)
_EPILOG = """\
A restore writes on the server and replaces what is at the destination under the
same names; the previous version is NOT kept. The command first lists what would
be replaced and asks you to type the server identifier; a server this client
does not manage needs the identifier typed a second time. Nothing is written
before that. The copy is compared with the source by checksum and is reported as
done only when equal. A report is saved in --report-dir (default: the current
directory).

Without a terminal on standard input nothing is asked: --yes is required (the
identifier on the command line is then the identifier typed) and, for a server
not managed by this client, --confirm-unmanaged <identifier>.
"""


def is_restore_request(argv: Sequence[str]) -> bool:
    """Whether ``argv`` is ``server restore ...`` and not ``server <cfg> <id>``."""
    return is_verb_request(argv, RESTORE)


def build_parser() -> argparse.ArgumentParser:
    """Build the parser of the restore command."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Copy local files to a server and verify them.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verbs = parser.add_subparsers(dest="verb", metavar="action", required=True)
    sub = verbs.add_parser(
        RESTORE,
        help="copy local paths to a server directory (replaces, keeps no copy)",
        description="Copy local files to a server and verify them.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub.add_argument("configuration", help="identifier or display name")
    sub.add_argument("identifier", help="server identifier at the provider")
    sub.add_argument("local_paths", nargs="+", metavar="local-path")
    sub.add_argument("--to", required=True, metavar="REMOTE_DIR", dest="destination")
    sub.add_argument("--report-dir", default=".", metavar="DIR")
    sub.add_argument(
        "--yes",
        action="store_true",
        help="confirm without a terminal; the identifier given is the one typed",
    )
    sub.add_argument(
        "--confirm-unmanaged",
        metavar="IDENTIFIER",
        help="the identifier of a server not managed by this client, typed again",
    )
    add_accept_host_key(sub)
    sub.set_defaults(handler=run)
    return parser


def run(arguments: argparse.Namespace, services: CliServices) -> int:
    """Show what would be replaced, confirm, copy, verify and report."""
    restore = build_restore(services)
    preview = restore.preview_restore(
        arguments.configuration,
        arguments.identifier,
        arguments.local_paths,
        arguments.destination,
        arguments.accept_host_key,
    )
    if preview.outcome is not TransferOutcome.READY:
        return preview_failure(services, "restore", preview, arguments.configuration)
    _write_restore_preview(services, preview)
    confirmation = _confirmation(services, arguments, preview)
    result = restore.request_restore(
        arguments.configuration,
        arguments.identifier,
        arguments.local_paths,
        arguments.destination,
        arguments.report_dir,
        confirmation,
    )
    if result.report is not None:
        return write_report(services, result)
    return _not_written(services, arguments, preview, result.outcome, result.reason)


def _write_restore_preview(services: CliServices, preview: TransferPreview) -> None:
    out = services.stdout
    write_line(
        out,
        f"About to restore {len(preview.paths)} path(s) to server "
        f"{preview.server_identifier!r}.",
    )
    write_preview(services, preview)
    exists = "exists" if preview.destination_exists else "will be created"
    write_detail_line(out, "Remote dir", f"{preview.destination} ({exists})")
    if preview.overwrites:
        write_line(out, "These items on the server would be replaced:")
        for entry in preview.overwrites:
            write_detail_line(
                out,
                "Replace",
                f"{entry.path} ({entry.size} bytes, changed "
                f"{entry.modified_at.isoformat()})",
            )
    else:
        write_line(out, "Nothing on the server would be replaced.")
    write_line(out, NOT_KEPT)
    if not preview.managed:
        write_line(out, "This server is not managed by this client.")


def _confirmation(
    services: CliServices, arguments: argparse.Namespace, preview: TransferPreview
) -> Confirmation:
    """The typed identifier and, when needed, the identifier typed again."""
    if not services.is_interactive:
        return Confirmation(
            given=arguments.yes,
            typed_identifier=arguments.identifier,
            unmanaged_identifier=arguments.confirm_unmanaged,
        )
    typed = services.read_line(
        f"Type the server identifier {preview.server_identifier!r} to restore: "
    ).strip()
    if not typed:
        return Confirmation(given=False)
    again = arguments.confirm_unmanaged
    if not preview.managed and again is None and typed == preview.server_identifier:
        again = services.read_line(
            "This server is not managed by this client; type its identifier "
            "again to confirm: "
        ).strip()
    return Confirmation(
        given=True, typed_identifier=typed, unmanaged_identifier=again or None
    )


def _not_written(
    services: CliServices,
    arguments: argparse.Namespace,
    preview: TransferPreview,
    outcome: TransferOutcome,
    reason: str,
) -> int:
    err = services.stderr
    if outcome is TransferOutcome.NOT_CONFIRMED and not services.is_interactive:
        write_line(
            err,
            f"error: the restore to server {preview.server_identifier!r} needs "
            "confirmation and no terminal is available to ask",
        )
        needed = "--yes"
        if not preview.managed:
            needed += f" --confirm-unmanaged {preview.server_identifier}"
        write_line(err, f"Hint: repeat the command with {needed}. Nothing was written.")
        return exit_codes.FAILURE
    return write_failure(
        services,
        "restore",
        outcome,
        reason,
        arguments.configuration,
        preview.server_identifier,
        preview.host_key,
    )
