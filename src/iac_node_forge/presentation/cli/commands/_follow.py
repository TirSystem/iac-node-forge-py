"""Following an operation to its end and reporting how it ended."""

import argparse
from typing import TextIO

from iac_node_forge.application.dto import (
    OperationSummary,
    ProvisionOutcome,
    ProvisionResult,
)
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
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands._provisioning import (
    build_provision_server,
)
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
    """Follow the operation, print it and return the exit code of its end.

    A provision operation that has ended is also completed: the server is
    recorded as managed, or the state left at the provider is reported.
    """
    tracker = TrackOperation(
        resolver,
        services.configurations,
        services.operations,
        services.clock,
        services.sleep,
    )
    operation = tracker.follow_operation(operation_id, time_limit, poll_interval)
    return report_end(services, resolver, operation)


def report_end(
    services: CliServices, resolver: CliProviderResolver, operation: OperationSummary
) -> int:
    """Print an operation that is not being followed and return its exit code."""
    completion = complete_provision(services, resolver, operation)
    if operation.state is OperationState.SUCCEEDED and _is_recorded(completion):
        write_operation(
            services.stdout,
            operation,
            _server_state(services, resolver, operation),
        )
        if completion is not None:
            write_completion(services.stdout, completion)
        return exit_codes.SUCCESS
    write_operation(services.stdout, operation)
    if operation.state is OperationState.RUNNING:
        return exit_codes.SUCCESS
    _write_failure(services, operation, completion)
    return exit_codes.FAILURE


def complete_provision(
    services: CliServices, resolver: CliProviderResolver, operation: OperationSummary
) -> ProvisionResult | None:
    """Complete a provision operation that has ended; None for any other."""
    if (
        operation.action is not PowerAction.PROVISION
        or operation.state is OperationState.RUNNING
    ):
        return None
    return build_provision_server(services, resolver).complete_provisioning(
        operation.operation_id
    )


def write_completion(stream: TextIO, completion: ProvisionResult) -> None:
    """Say how the completion of a provision ended, after the operation block."""
    if completion.outcome is ProvisionOutcome.COMPLETED:
        write_line(
            stream,
            f"Managed: server {completion.server_identifier!r} is recorded as "
            "managed by this client.",
        )
        return
    if completion.reason:
        write_line(stream, f"Provisioning: {completion.reason}")
    if completion.state_left:
        write_line(stream, f"State left at the provider: {completion.state_left}")


def _is_recorded(completion: ProvisionResult | None) -> bool:
    """Whether the end counts as a success: not a provision, or a recorded one."""
    return completion is None or completion.outcome is ProvisionOutcome.COMPLETED


def _server_state(
    services: CliServices, resolver: CliProviderResolver, operation: OperationSummary
) -> str | None:
    """Read the server's state after success; a failed read is not an error."""
    if operation.server_identifier is None:
        return None
    try:
        server = ListServers(resolver, services.configurations).inspect_server(
            operation.configuration_id, operation.server_identifier
        )
    except (ProviderError, CredentialStoreError, UseCaseError):
        return None
    return server.state


def _write_failure(
    services: CliServices,
    operation: OperationSummary,
    completion: ProvisionResult | None = None,
) -> None:
    action = operation.action.value
    of_server = _of_server(operation)
    detail = operation.detail or "the provider gave no reason"
    err = services.stderr
    if operation.state is OperationState.FAILED:
        write_line(err, f"error: the {action}{of_server} failed: {detail}")
        if completion is not None and completion.state_left:
            write_line(err, f"State left at the provider: {completion.state_left}")
        write_line(
            err,
            f"Hint: the request was not repeated; {_check_hint(operation)}.",
        )
        return
    if operation.state is OperationState.SUCCEEDED and completion is not None:
        write_line(
            err, f"error: the {action}{of_server} is incomplete: {completion.reason}"
        )
        write_line(err, f"Hint: {_check_hint(operation)}.")
        return
    write_line(
        err,
        f"error: the outcome of the {action}{of_server} is unknown: {detail}",
    )
    write_line(
        err,
        f"Hint: the request was sent and is not repeated; {_check_hint(operation)}; "
        f"the record is shown by 'iac-node-forge operation {operation.operation_id}'.",
    )


def _of_server(operation: OperationSummary) -> str:
    """`` of server 'x'`` when the provider named the server, otherwise nothing."""
    if operation.server_identifier is None:
        return ""
    return f" of server {operation.server_identifier!r}"


def _check_hint(operation: OperationSummary) -> str:
    if operation.server_identifier is None:
        return (
            "list the servers with 'iac-node-forge servers "
            f"--configuration {operation.configuration_id}'"
        )
    return (
        "check the server with "
        f"'iac-node-forge server {operation.configuration_id} "
        f"{operation.server_identifier}'"
    )
