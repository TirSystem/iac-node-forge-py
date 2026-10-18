"""``server start|shutdown|stop|restart``: UC-008 and UC-006 at the command line.

The group shares the word ``server`` with the read command
``server <configuration> <identifier>``. ``is_power_request`` tells them
apart before parsing, so the read command keeps working unchanged.
"""

import argparse
from collections.abc import Sequence

from iac_node_forge.application.dto import (
    Confirmation,
    OperationSummary,
    PowerActionResult,
    PowerOutcome,
)
from iac_node_forge.application.use_cases import ControlServerPower
from iac_node_forge.domain.power_action import POWER_ACTIONS, PowerAction
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands._follow import (
    add_wait_options,
    follow_and_report,
)
from iac_node_forge.presentation.cli.operation_output import write_operation
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.provider_resolver import (
    CliProviderResolver,
    build_resolver,
)
from iac_node_forge.presentation.cli.services import CliServices

PROGRAM = "iac-node-forge server"
VERBS: tuple[str, ...] = tuple(action.value for action in POWER_ACTIONS)

_NOT_MANAGED_NOTICE = "This server is not managed by this client."
_YES_ANSWERS = frozenset({"y", "yes"})

_SENTENCE = {
    PowerAction.START: "start",
    PowerAction.SHUTDOWN: "shut down",
    PowerAction.STOP: "stop",
    PowerAction.RESTART: "restart",
}
_WHAT_HAPPENS = {
    PowerAction.START: "The server is powered on.",
    PowerAction.SHUTDOWN: (
        "The operating system is asked to shut down; the server may take a "
        "while to stop."
    ),
    PowerAction.STOP: (
        "The server is powered off at once, as when the power is cut. "
        "Unsaved data may be lost."
    ),
    PowerAction.RESTART: (
        "The server is restarted and its services are interrupted. "
        "Unsaved data may be lost."
    ),
}
_HELP = {
    PowerAction.START: "power on a server",
    PowerAction.SHUTDOWN: "ask a server to shut down gracefully",
    PowerAction.STOP: "power a server off at once (unsaved data may be lost)",
    PowerAction.RESTART: "restart a server (unsaved data may be lost)",
}
_EPILOG = """\
Without a terminal on standard input nothing is asked: --yes is required and,
for a server not managed by this client, --confirm-unmanaged <identifier>;
without them no request is sent and the exit code is 1.

The state of the server is read first. A server already in the requested state
(start of a running server, shutdown or stop of a stopped one) is reported
and nothing is sent or recorded.
"""


def is_power_request(argv: Sequence[str]) -> bool:
    """Whether ``argv`` is a power command rather than ``server <cfg> <id>``.

    ``server start lab`` with exactly three words is the read command for a
    configuration that is named ``start``; a power command has at least a
    configuration and an identifier after its verb, or an option such as
    ``--help`` in the third place.
    """
    if len(argv) < 2 or argv[0] != "server" or argv[1] not in VERBS:
        return False
    return len(argv) != 3 or argv[2].startswith("-")


def build_parser() -> argparse.ArgumentParser:
    """Build the parser of the power commands."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Change the power state of a server and follow the operation.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verbs = parser.add_subparsers(dest="verb", metavar="action", required=True)
    for action in POWER_ACTIONS:
        sub = verbs.add_parser(
            action.value,
            help=_HELP[action],
            description=_WHAT_HAPPENS[action],
            epilog=_EPILOG,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        sub.add_argument("configuration", help="identifier or display name")
        sub.add_argument("identifier", help="server identifier at the provider")
        sub.add_argument(
            "--yes", action="store_true", help="confirm the action without asking"
        )
        sub.add_argument(
            "--confirm-unmanaged",
            metavar="IDENTIFIER",
            help="the identifier of a server not managed by this client, as "
            "the extra confirmation without asking",
        )
        sub.add_argument(
            "--no-wait",
            action="store_true",
            help="print the operation and exit; follow it with "
            "'iac-node-forge operation <id> --follow'",
        )
        add_wait_options(sub)
        sub.set_defaults(handler=run, action=action)
    return parser


def run(arguments: argparse.Namespace, services: CliServices) -> int:
    """Confirm, request one power action, and follow it unless told not to."""
    resolver = build_resolver(services)
    control = ControlServerPower(resolver, services.configurations, services.operations)
    action: PowerAction = arguments.action
    if services.is_interactive:
        preview = control.preview_power_action(
            arguments.configuration, arguments.identifier, action
        )
        if preview.outcome is not PowerOutcome.READY:
            return _not_sent(services, preview, interactive=True)
        confirmation = _ask(services, preview, arguments)
    else:
        confirmation = Confirmation(arguments.yes, arguments.confirm_unmanaged)
    result = control.request_power_action(
        arguments.configuration, arguments.identifier, action, confirmation
    )
    operation = result.operation
    if operation is None:
        return _not_sent(services, result, interactive=services.is_interactive)
    if result.outcome is PowerOutcome.REJECTED:
        return _rejected(services, result, operation)
    if arguments.no_wait:
        write_operation(services.stdout, operation)
        write_line(
            services.stdout,
            "Follow it with: "
            f"iac-node-forge operation {operation.operation_id} --follow",
        )
        return exit_codes.SUCCESS
    return _follow(services, resolver, result, operation, arguments)


def _follow(
    services: CliServices,
    resolver: CliProviderResolver,
    result: PowerActionResult,
    operation: OperationSummary,
    arguments: argparse.Namespace,
) -> int:
    write_line(
        services.stdout,
        f"Requested: {_SENTENCE[result.action]} of server "
        f"{result.server_identifier!r} at {result.provider_name!r}; following "
        f"the operation for at most {arguments.timeout:g} seconds.",
    )
    return follow_and_report(
        services,
        resolver,
        operation.operation_id,
        arguments.timeout,
        arguments.poll_interval,
    )


def _ask(
    services: CliServices, preview: PowerActionResult, arguments: argparse.Namespace
) -> Confirmation:
    """State what will happen and ask; ``--yes`` and the typed id skip a question."""
    out = services.stdout
    name = f" ({preview.server_name})" if preview.server_name else ""
    write_line(
        out,
        f"About to {_SENTENCE[preview.action]} server "
        f"{preview.server_identifier!r}{name} at provider "
        f"{preview.provider_name!r}; it is {preview.server_state}.",
    )
    write_line(out, _WHAT_HAPPENS[preview.action])
    if not preview.managed:
        write_line(out, _NOT_MANAGED_NOTICE)
    if not arguments.yes:
        answer = services.read_line(
            f"Do you want to {_SENTENCE[preview.action]} this server? [yes/no]: "
        )
        if answer.strip().casefold() not in _YES_ANSWERS:
            return Confirmation(given=False)
    if preview.managed:
        return Confirmation(given=True)
    typed = arguments.confirm_unmanaged
    if typed is None:
        typed = services.read_line(
            f"Retype the server identifier {preview.server_identifier!r} to confirm: "
        ).strip()
    return Confirmation(given=True, typed_identifier=typed)


def _not_sent(
    services: CliServices, result: PowerActionResult, interactive: bool
) -> int:
    """Report an outcome in which no request was sent; the exit code is 1."""
    where = f"{result.provider_name!r}"
    server = f"server {result.server_identifier!r}"
    action = _SENTENCE[result.action]
    err = services.stderr
    outcome = result.outcome
    if outcome is PowerOutcome.NOT_FOUND:
        write_line(err, f"error: {server} was not found at provider {where}")
        write_line(
            err,
            "Hint: list the servers with 'iac-node-forge servers "
            f"--configuration {result.configuration_id}'. No request was sent.",
        )
    elif outcome is PowerOutcome.UNSUPPORTED:
        write_line(err, f"error: cannot {action} {server}: {result.reason}")
        write_line(
            err,
            "Hint: 'iac-node-forge capabilities "
            f"{result.configuration_id}' shows what the provider supports. "
            "No request was sent.",
        )
    elif outcome is PowerOutcome.ALREADY_IN_STATE:
        write_line(
            err,
            f"error: nothing to do: {server} at provider {where} is already "
            f"{result.server_state}",
        )
        write_line(err, "Hint: no request was sent and nothing was recorded.")
    elif outcome is PowerOutcome.IDENTIFIER_MISMATCH:
        write_line(err, f"error: {result.reason}; the {action} was cancelled")
        write_line(err, f"Hint: type {result.server_identifier!r} exactly.")
    else:
        _not_confirmed(services, result, interactive)
    return exit_codes.FAILURE


def _not_confirmed(
    services: CliServices, result: PowerActionResult, interactive: bool
) -> None:
    action = _SENTENCE[result.action]
    err = services.stderr
    if interactive:
        write_line(err, f"error: the {action} was not confirmed and was cancelled")
        write_line(err, "Hint: no request was sent.")
        return
    write_line(
        err,
        f"error: the {action} of server {result.server_identifier!r} needs "
        "confirmation and no terminal is available to ask",
    )
    needed = "--yes"
    if not result.managed:
        needed += f" --confirm-unmanaged {result.server_identifier}"
        write_line(err, _NOT_MANAGED_NOTICE)
    write_line(err, f"Hint: repeat the command with {needed}. No request was sent.")


def _rejected(
    services: CliServices, result: PowerActionResult, operation: OperationSummary
) -> int:
    write_operation(services.stdout, operation)
    write_line(
        services.stderr,
        f"error: the provider rejected the {_SENTENCE[result.action]} of "
        f"server {result.server_identifier!r}: {result.reason}",
    )
    write_line(
        services.stderr,
        "Hint: check that the credentials may change power state of servers; "
        "the request was not repeated.",
    )
    return exit_codes.FAILURE
