"""``server define|provision|definitions``: UC-003 and UC-004 at the command line.

The group shares the word ``server`` with the read command
``server <configuration> <identifier>`` and with the power commands.
``is_provision_request`` tells them apart before parsing, as
``server_power.is_power_request`` does, so the read command keeps working.
"""

import argparse
from collections.abc import Sequence

from iac_node_forge.application.dto import (
    Confirmation,
    DesiredConfigurationView,
    OperationSummary,
    ProvisionOutcome,
    ProvisionPreview,
    ProvisionResult,
)
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands import Subparsers
from iac_node_forge.presentation.cli.commands._follow import (
    add_wait_options,
    follow_and_report,
)
from iac_node_forge.presentation.cli.commands._provisioning import (
    build_define,
    build_provision_server,
)
from iac_node_forge.presentation.cli.operation_output import (
    write_detail_line,
    write_operation,
)
from iac_node_forge.presentation.cli.output import NO_VALUE, format_table, write_line
from iac_node_forge.presentation.cli.provider_resolver import build_resolver
from iac_node_forge.presentation.cli.provider_words import words_for
from iac_node_forge.presentation.cli.services import CliServices

PROGRAM = "iac-node-forge server"
DEFINE = "define"
PROVISION = "provision"
DEFINITIONS = "definitions"
VERBS: tuple[str, ...] = (DEFINE, PROVISION, DEFINITIONS)

_YES_ANSWERS = frozenset({"y", "yes"})
_NO_COST = "none; the provider imposes no commitment"

_EPILOG = """\
A desired configuration is a named template: each 'server provision' creates one
server from it, named by --name or, when absent, by the configuration name.
Values are checked against what the provider offers when they are defined and
again at provisioning. The image is the template (Proxmox VE) or the image
(Contabo), as the provider offers it ('iac-node-forge options <configuration>
<kind>'); the access keys are names only, never a key value.

Without a terminal on standard input nothing is asked: 'server provision'
needs --yes, and without it no request is sent and the exit code is 1.
"""


def is_provision_request(argv: Sequence[str]) -> bool:
    """Whether ``argv`` is a definition or provisioning command.

    ``server define lab`` with exactly three words is the read command for a
    configuration that is named ``define``; ``definitions`` takes only the
    configuration, so three words are already its command.
    """
    if len(argv) < 2 or argv[0] != "server" or argv[1] not in VERBS:
        return False
    if argv[1] == DEFINITIONS:
        return True
    return len(argv) != 3 or argv[2].startswith("-")


def build_parser() -> argparse.ArgumentParser:
    """Build the parser of the definition and provisioning commands."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Define a desired server configuration and create a server "
        "from it.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verbs = parser.add_subparsers(dest="verb", metavar="action", required=True)
    _add_define(verbs)
    _add_provision(verbs)
    _add_definitions(verbs)
    return parser


def _add_define(verbs: Subparsers) -> None:
    sub = verbs.add_parser(
        DEFINE,
        help="check and save a named desired server configuration",
        description="Check a description of the server wanted against what the "
        "provider offers and save it under a name. A description that is not "
        "valid is reported and not saved; a description with the same name is "
        "replaced.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub.add_argument("configuration", help="identifier or display name")
    sub.add_argument("name", help="name of the desired configuration")
    sub.add_argument("--image", required=True, help="template or image to install")
    sub.add_argument(
        "--key",
        action="append",
        default=[],
        metavar="NAME",
        help="name of an access key to install (repeatable); never its value",
    )
    sub.add_argument("--hint", help="bootstrap hint (at most 500 characters)")
    sub.add_argument(
        "--set",
        action="append",
        default=[],
        type=_name_value,
        metavar="NAME=VALUE",
        help="a provider-specific value (repeatable), such as node, storage, "
        "region, product or period",
    )
    sub.set_defaults(handler=run_define)


def _add_provision(verbs: Subparsers) -> None:
    sub = verbs.add_parser(
        PROVISION,
        help="create a server from a desired configuration",
        description="Show what will be created and what it commits you to, ask "
        "for confirmation, send one create request and follow the operation.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub.add_argument("configuration", help="identifier or display name")
    sub.add_argument("desired_name", help="name of the desired configuration")
    sub.add_argument(
        "--name",
        metavar="SERVER_NAME",
        help="name of the server (default: the desired configuration name)",
    )
    sub.add_argument(
        "--yes", action="store_true", help="confirm the creation without asking"
    )
    sub.add_argument(
        "--no-wait",
        action="store_true",
        help="print the operation and exit; follow it with "
        "'iac-node-forge operation <id> --follow'",
    )
    add_wait_options(sub)
    sub.set_defaults(handler=run_provision)


def _add_definitions(
    verbs: Subparsers,
) -> None:
    sub = verbs.add_parser(
        DEFINITIONS,
        help="list the desired configurations of a provider",
        description="List the saved desired configurations of a provider.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub.add_argument("configuration", help="identifier or display name")
    sub.add_argument(
        "--check",
        action="store_true",
        help="check each against what the provider offers now",
    )
    sub.set_defaults(handler=run_definitions)


def _name_value(text: str) -> tuple[str, str]:
    name, separator, value = text.partition("=")
    if not separator or not name.strip() or not value.strip():
        raise argparse.ArgumentTypeError(f"{text!r} is not NAME=VALUE")
    return name.strip(), value.strip()


def run_define(arguments: argparse.Namespace, services: CliServices) -> int:
    """Check and save a desired configuration; print it or its problems."""
    pairs: list[tuple[str, str]] = arguments.set
    values = dict(pairs)
    if len(values) != len(pairs):
        write_line(services.stderr, "error: a value is given more than once")
        return exit_codes.USAGE
    view = build_define(
        services, build_resolver(services)
    ).define_desired_configuration(
        arguments.configuration,
        arguments.name,
        arguments.image,
        arguments.key,
        arguments.hint,
        values,
    )
    if not view.valid:
        write_line(
            services.stderr,
            f"error: desired configuration {view.name!r} is not valid and was "
            "not saved",
        )
        _write_problems(services, view)
        write_line(
            services.stderr,
            "Hint: 'iac-node-forge options "
            f"{arguments.configuration} <kind>' shows what the provider offers.",
        )
        return exit_codes.FAILURE
    write_line(
        services.stdout,
        f"Desired configuration {view.name!r} is valid and saved.",
    )
    _write_view(services, view)
    return exit_codes.SUCCESS


def run_definitions(arguments: argparse.Namespace, services: CliServices) -> int:
    """List the saved desired configurations; with --check, re-check them."""
    views = build_define(
        services, build_resolver(services)
    ).list_desired_configurations(arguments.configuration, arguments.check)
    if not views:
        write_line(services.stdout, "No desired configurations found.")
        return exit_codes.SUCCESS
    headers = ["Name", "Image", "Keys", "Values"]
    if arguments.check:
        headers.append("Valid")
    rows = [
        [
            view.name,
            view.image,
            ", ".join(view.access_key_names) or NO_VALUE,
            _values_text(view),
            *(["yes" if view.valid else "no"] if arguments.check else []),
        ]
        for view in views
    ]
    for line in format_table(headers, rows):
        write_line(services.stdout, line)
    invalid = [view for view in views if not view.valid]
    for view in invalid:
        _write_problems(services, view)
    return exit_codes.FAILURE if invalid else exit_codes.SUCCESS


def run_provision(arguments: argparse.Namespace, services: CliServices) -> int:
    """Show the preview, confirm, request one creation and follow it."""
    resolver = build_resolver(services)
    provision = build_provision_server(services, resolver)
    server_name = arguments.name or ""
    preview = provision.preview_provisioning(
        arguments.configuration, arguments.desired_name, server_name
    )
    if preview.outcome is not ProvisionOutcome.READY:
        return _not_sent(services, preview.outcome, preview.reason, interactive=False)
    _write_preview(services, preview)
    confirmation = _ask(services, arguments, preview)
    result = provision.request_provisioning(
        arguments.configuration, arguments.desired_name, server_name, confirmation
    )
    operation = result.operation
    if operation is None:
        return _not_sent(
            services,
            result.outcome,
            result.reason,
            interactive=services.is_interactive,
        )
    if result.outcome is ProvisionOutcome.REJECTED:
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
        f"Requested: creation of server {preview.server_name!r} at "
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


def _write_preview(services: CliServices, preview: ProvisionPreview) -> None:
    out = services.stdout
    label = words_for(preview.provider_kind).label
    write_line(
        out,
        f"About to create server {preview.server_name!r} at provider "
        f"{preview.provider_name!r}.",
    )
    write_detail_line(out, "Provider", f"{preview.provider_name} ({label})")
    write_detail_line(out, "Configuration", preview.desired_name)
    write_detail_line(out, "Server name", preview.server_name)
    write_detail_line(out, "Target", preview.target or NO_VALUE)
    write_detail_line(out, "Image", preview.image)
    write_detail_line(out, "Size", preview.size or NO_VALUE)
    write_detail_line(
        out,
        "Values",
        ", ".join(f"{v.name}={v.value}" for v in preview.values) or NO_VALUE,
    )
    write_detail_line(out, "Cost", preview.cost_consequence or _NO_COST)


def _ask(
    services: CliServices, arguments: argparse.Namespace, preview: ProvisionPreview
) -> Confirmation:
    """The yes to the creation: ``--yes``, an answer at a terminal, else none."""
    if arguments.yes:
        return Confirmation(given=True)
    if not services.is_interactive:
        return Confirmation(given=False)
    answer = services.read_line(
        f"Do you want to create server {preview.server_name!r}"
        + (" and accept the cost above" if preview.cost_consequence else "")
        + "? [yes/no]: "
    )
    return Confirmation(given=answer.strip().casefold() in _YES_ANSWERS)


def _not_sent(
    services: CliServices, outcome: ProvisionOutcome, reason: str, interactive: bool
) -> int:
    """Report an outcome in which no request was sent; the exit code is 1."""
    err = services.stderr
    if outcome is ProvisionOutcome.NOT_CONFIRMED:
        _not_confirmed(services, interactive)
        return exit_codes.FAILURE
    write_line(err, f"error: no server was created: {reason}")
    write_line(err, f"Hint: {_hint(outcome)} No request was sent.")
    return exit_codes.FAILURE


def _hint(outcome: ProvisionOutcome) -> str:
    if outcome is ProvisionOutcome.INVALID:
        return (
            "fix the description with 'iac-node-forge server define' "
            "(see 'iac-node-forge server definitions <configuration> --check')."
        )
    if outcome is ProvisionOutcome.UNSUPPORTED:
        return (
            "'iac-node-forge capabilities <configuration>' shows what the "
            "provider supports."
        )
    if outcome is ProvisionOutcome.NAME_EXISTS:
        return "choose another server name with --name."
    return (
        "follow the earlier request with 'iac-node-forge operation <id> "
        "--follow' or choose another server name with --name."
    )


def _not_confirmed(services: CliServices, interactive: bool) -> None:
    err = services.stderr
    if interactive:
        write_line(err, "error: the creation was not confirmed and was cancelled")
        write_line(err, "Hint: no request was sent.")
        return
    write_line(
        err,
        "error: the creation needs confirmation and no terminal is available to ask",
    )
    write_line(err, "Hint: repeat the command with --yes. No request was sent.")


def _rejected(
    services: CliServices, result: ProvisionResult, operation: OperationSummary
) -> int:
    write_operation(services.stdout, operation)
    err = services.stderr
    write_line(err, f"error: the provider rejected the creation: {result.reason}")
    if result.state_left:
        write_line(err, f"State left at the provider: {result.state_left}")
    write_line(
        err,
        "Hint: check that the credentials may create servers; the request was "
        "not repeated.",
    )
    return exit_codes.FAILURE


def _write_view(services: CliServices, view: DesiredConfigurationView) -> None:
    out = services.stdout
    write_detail_line(out, "Image", view.image)
    write_detail_line(out, "Keys", ", ".join(view.access_key_names) or NO_VALUE)
    write_detail_line(out, "Hint", view.bootstrap_hint or NO_VALUE)
    write_detail_line(out, "Values", _values_text(view))


def _write_problems(services: CliServices, view: DesiredConfigurationView) -> None:
    for problem in view.problems:
        write_line(services.stderr, f"  - {view.name}: {problem}")


def _values_text(view: DesiredConfigurationView) -> str:
    return ", ".join(f"{v.name}={v.value}" for v in view.values) or NO_VALUE
