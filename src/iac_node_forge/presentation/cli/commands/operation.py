"""``operation``: show a recorded operation, or follow one that is running."""

import argparse

from iac_node_forge.application.use_cases import TrackOperation
from iac_node_forge.domain.operation import OperationState
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands import Subparsers
from iac_node_forge.presentation.cli.commands._follow import (
    add_wait_options,
    complete_operation,
    follow_and_report,
    write_completion,
)
from iac_node_forge.presentation.cli.operation_output import write_operation
from iac_node_forge.presentation.cli.provider_resolver import build_resolver
from iac_node_forge.presentation.cli.services import CliServices


def add_parser(subparsers: Subparsers) -> None:
    """Register ``operation``."""
    parser = subparsers.add_parser(
        "operation",
        help="show a recorded operation (a power action, a provision or a removal)",
        description="Show the state, times and detail of a recorded operation. "
        "With --follow, an operation that is still running is followed to its "
        "end first. A provision or a removal that has ended is also completed: the "
        "server is recorded as managed or its record is ended, or the state left at "
        "the provider is shown.",
    )
    parser.add_argument(
        "operation_id",
        help="identifier printed by the power, provision or remove command",
    )
    parser.add_argument(
        "--follow",
        action="store_true",
        help="follow a running operation to its end, then show it",
    )
    add_wait_options(parser)
    parser.set_defaults(handler=run)


def run(arguments: argparse.Namespace, services: CliServices) -> int:
    """Print the recorded operation; with ``--follow`` follow it first."""
    resolver = build_resolver(services)
    tracker = TrackOperation(
        resolver,
        services.configurations,
        services.operations,
        services.clock,
        services.sleep,
    )
    operation = tracker.show_operation(arguments.operation_id)
    if arguments.follow and operation.state is OperationState.RUNNING:
        return follow_and_report(
            services,
            resolver,
            arguments.operation_id,
            arguments.timeout,
            arguments.poll_interval,
        )
    write_operation(services.stdout, operation)
    completion = complete_operation(services, resolver, operation)
    if completion is not None:
        write_completion(services.stdout, completion)
    return exit_codes.SUCCESS
