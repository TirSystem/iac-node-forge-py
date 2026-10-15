"""Following an operation to its end and reporting how it ended."""

import argparse

from iac_node_forge.application.dto import OperationSummary
from iac_node_forge.application.errors import (
    CredentialStoreError,
    ProviderError,
    UseCaseError,
)
from iac_node_forge.application.use_cases import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_TIME_LIMIT_SECONDS,
    ListServers,
    TrackOperation,
)
from iac_node_forge.domain.operation import OperationState
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.operation_output import write_operation
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.provider_resolver import CliProviderResolver
from iac_node_forge.presentation.cli.services import CliServices


def _non_negative_seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not value >= 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def _positive_seconds(text: str) -> float:
    value = _non_negative_seconds(text)
    if value == 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def add_wait_options(parser: argparse.ArgumentParser) -> None:
    """Add ``--timeout`` and ``--poll-interval``, shared by the commands that wait."""
    parser.add_argument(
        "--timeout",
        type=_non_negative_seconds,
        default=DEFAULT_TIME_LIMIT_SECONDS,
        metavar="SECONDS",
        help="longest time to follow the operation; when it passes the outcome "
        "is reported as unknown and nothing is repeated "
        f"(default {DEFAULT_TIME_LIMIT_SECONDS:g})",
    )
    parser.add_argument(
        "--poll-interval",
        type=_positive_seconds,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        metavar="SECONDS",
        help="time between two reads of the operation "
        f"(default {DEFAULT_POLL_INTERVAL_SECONDS:g})",
    )


def follow_and_report(
    services: CliServices,
    resolver: CliProviderResolver,
    operation_id: str,
    time_limit: float,
    poll_interval: float,
) -> int:
    """Follow the operation, print it and return the exit code of its end."""
    tracker = TrackOperation(
        resolver,
        services.configurations,
        services.operations,
        services.clock,
        services.sleep,
    )
    operation = tracker.follow_operation(operation_id, time_limit, poll_interval)
    if operation.state is OperationState.SUCCEEDED:
        write_operation(
            services.stdout,
            operation,
            _server_state(services, resolver, operation),
        )
        return exit_codes.SUCCESS
    write_operation(services.stdout, operation)
    if operation.state is OperationState.RUNNING:
        return exit_codes.SUCCESS
    _write_failure(services, operation)
    return exit_codes.FAILURE


def _server_state(
    services: CliServices, resolver: CliProviderResolver, operation: OperationSummary
) -> str | None:
    """Read the server's state after success; a failed read is not an error."""
    try:
        server = ListServers(resolver, services.configurations).inspect_server(
            operation.configuration_id, operation.server_identifier
        )
    except (ProviderError, CredentialStoreError, UseCaseError):
        return None
    return server.state


def _write_failure(services: CliServices, operation: OperationSummary) -> None:
    action = operation.action.value
    detail = operation.detail or "the provider gave no reason"
    if operation.state is OperationState.FAILED:
        write_line(
            services.stderr,
            f"error: the {action} of server {operation.server_identifier!r} "
            f"failed: {detail}",
        )
        write_line(
            services.stderr,
            "Hint: the request was not repeated; check the server with "
            f"'iac-node-forge server {operation.configuration_id} "
            f"{operation.server_identifier}'.",
        )
        return
    write_line(
        services.stderr,
        f"error: the outcome of the {action} of server "
        f"{operation.server_identifier!r} is unknown: {detail}",
    )
    write_line(
        services.stderr,
        "Hint: the request was sent and is not repeated; check the server with "
        f"'iac-node-forge server {operation.configuration_id} "
        f"{operation.server_identifier}'; the record is shown by "
        f"'iac-node-forge operation {operation.operation_id}'.",
    )
