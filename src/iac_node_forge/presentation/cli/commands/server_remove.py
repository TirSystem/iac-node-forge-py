"""``server remove``: UC-012 and UC-006 at the command line.

The word ``server`` is shared with the read command
``server <configuration> <identifier>`` and with the power and provisioning
commands. ``is_remove_request`` tells them apart before parsing, as
``server_power.is_power_request`` does, so the read command keeps working.
"""

import argparse
from collections.abc import Sequence

from iac_node_forge.application.dto import (
    Confirmation,
    OperationSummary,
    RemovalOutcome,
    RemovalPreview,
    RemovalResult,
)
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands._follow import (
    add_wait_options,
    follow_and_report,
)
from iac_node_forge.presentation.cli.commands._removal import build_remove_server
from iac_node_forge.presentation.cli.operation_output import (
    write_detail_line,
    write_operation,
)
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.provider_resolver import build_resolver
from iac_node_forge.presentation.cli.provider_words import words_for
from iac_node_forge.presentation.cli.services import CliServices

PROGRAM = "iac-node-forge server"
REMOVE = "remove"

_NOT_MANAGED_NOTICE = "This server is not managed by this client."
_EPILOG = """\
Removal is a write that cannot be undone. The command states what the provider
does (Proxmox VE destroys the server at once; Contabo requests a cancellation
and the contract and its billing run on until the effective date the provider
reports) and asks you to type the server identifier to confirm. A server this
client does not manage needs the identifier typed a second time.

A running server is refused and is never stopped for you: shut it down first.

Without a terminal on standard input nothing is asked: --yes is required (the
identifier on the command line is then the identifier typed) and, for a server
not managed by this client, --confirm-unmanaged <identifier>; without them no
request is sent and the exit code is 1. At a terminal the identifier is always
asked for, whatever --yes says.
"""


def is_remove_request(argv: Sequence[str]) -> bool:
    """Whether ``argv`` is a removal rather than ``server <cfg> <id>``.

    ``server remove lab`` with exactly three words is the read command for a
    configuration that is named ``remove``; a removal has at least a
    configuration and an identifier after its verb, or an option such as
    ``--help`` in the third place.
    """
    if len(argv) < 2 or argv[0] != "server" or argv[1] != REMOVE:
        return False
    return len(argv) != 3 or argv[2].startswith("-")


def build_parser() -> argparse.ArgumentParser:
    """Build the parser of the removal command."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Remove a server at its provider and follow the operation.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verbs = parser.add_subparsers(dest="verb", metavar="action", required=True)
    sub = verbs.add_parser(
        REMOVE,
        help="delete or cancel a server (it cannot be undone)",
        description="State what removal means at the provider, ask for "
        "confirmation, send one removal request and follow the operation.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub.add_argument("configuration", help="identifier or display name")
    sub.add_argument("identifier", help="server identifier at the provider")
    sub.add_argument(
        "--yes",
        action="store_true",
        help="confirm the removal without a terminal; the identifier given is "
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
    """Show the preview, confirm, request one removal and follow it."""
    resolver = build_resolver(services)
    remove = build_remove_server(services, resolver)
    preview = remove.preview_removal(arguments.configuration, arguments.identifier)
    _write_preview(services, preview)
    if preview.outcome is not RemovalOutcome.READY:
        return _not_sent(services, preview.outcome, preview.reason, preview, False)
    confirmation = _confirmation(services, arguments, preview)
    result = remove.request_removal(
        arguments.configuration, arguments.identifier, confirmation
    )
    operation = result.operation
    if operation is None:
        return _not_sent(
            services, result.outcome, result.reason, preview, services.is_interactive
        )
    if result.outcome is RemovalOutcome.REJECTED:
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
        f"Requested: removal of server {preview.server_identifier!r} at "
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


def _write_preview(services: CliServices, preview: RemovalPreview) -> None:
    """State what is known of the server and, when it is offered, the consequence."""
    out = services.stdout
    label = words_for(preview.provider_kind).label
    if preview.server_state is None:
        return
    name = f" ({preview.server_name})" if preview.server_name else ""
    write_line(
        out,
        f"About to remove server {preview.server_identifier!r}{name} at provider "
        f"{preview.provider_name!r}.",
    )
    write_detail_line(out, "Provider", f"{preview.provider_name} ({label})")
    write_detail_line(out, "Server", f"{preview.server_identifier}{name}")
    write_detail_line(out, "State", preview.server_state)
    write_detail_line(out, "Managed", "yes" if preview.managed else "no")
    if preview.kind is not None:
        write_detail_line(out, "Removal", preview.kind.value)
    if preview.consequence:
        write_detail_line(out, "Consequence", preview.consequence)
    if not preview.managed and preview.outcome is RemovalOutcome.READY:
        write_line(out, _NOT_MANAGED_NOTICE)


def _confirmation(
    services: CliServices, arguments: argparse.Namespace, preview: RemovalPreview
) -> Confirmation:
    """The typed identifier and, when needed, the identifier typed again."""
    if not services.is_interactive:
        return Confirmation(
            given=arguments.yes,
            typed_identifier=arguments.identifier,
            unmanaged_identifier=arguments.confirm_unmanaged,
        )
    typed = services.read_line(
        f"Type the server identifier {preview.server_identifier!r} to remove the "
        "server: "
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


def _not_sent(
    services: CliServices,
    outcome: RemovalOutcome,
    reason: str,
    preview: RemovalPreview,
    interactive: bool,
) -> int:
    """Report an outcome in which no request was sent; the exit code is 1."""
    err = services.stderr
    server = f"server {preview.server_identifier!r}"
    if outcome is RemovalOutcome.NOT_CONFIRMED:
        _not_confirmed(services, preview, interactive)
        return exit_codes.FAILURE
    write_line(err, f"error: {server} was not removed: {reason}")
    write_line(err, f"Hint: {_hint(outcome, preview)} No request was sent.")
    return exit_codes.FAILURE


def _hint(outcome: RemovalOutcome, preview: RemovalPreview) -> str:
    configuration = preview.configuration_id
    identifier = preview.server_identifier
    if outcome is RemovalOutcome.UNSUPPORTED:
        return (
            f"'iac-node-forge capabilities {configuration}' shows what the "
            "provider supports."
        )
    if outcome is RemovalOutcome.NOT_FOUND:
        return (
            "list the servers with 'iac-node-forge servers --configuration "
            f"{configuration}'."
        )
    if outcome is RemovalOutcome.RUNNING_REFUSED:
        return (
            "shut it down first with 'iac-node-forge server shutdown "
            f"{configuration} {identifier}'."
        )
    if outcome is RemovalOutcome.ALREADY_REMOVED:
        return "nothing is left to remove."
    return f"type {identifier!r} exactly."


def _not_confirmed(
    services: CliServices, preview: RemovalPreview, interactive: bool
) -> None:
    err = services.stderr
    if interactive:
        write_line(err, "error: the removal was not confirmed and was cancelled")
        write_line(err, "Hint: no request was sent.")
        return
    write_line(
        err,
        f"error: the removal of server {preview.server_identifier!r} needs "
        "confirmation and no terminal is available to ask",
    )
    needed = "--yes"
    if not preview.managed:
        needed += f" --confirm-unmanaged {preview.server_identifier}"
    write_line(err, f"Hint: repeat the command with {needed}. No request was sent.")


def _rejected(
    services: CliServices, result: RemovalResult, operation: OperationSummary
) -> int:
    write_operation(services.stdout, operation)
    err = services.stderr
    write_line(
        err,
        f"error: the provider rejected the removal of server "
        f"{operation.server_identifier!r}: {result.reason}",
    )
    if result.state_left:
        write_line(err, f"State left at the provider: {result.state_left}")
    write_line(
        err,
        "Hint: check that the credentials may remove servers; the request was "
        "not repeated.",
    )
    return exit_codes.FAILURE
