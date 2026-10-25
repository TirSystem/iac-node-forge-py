"""Following an operation to its end and reporting how it ended."""

import argparse
from dataclasses import dataclass
from typing import TextIO

from iac_node_forge.application.backup_dto import (
    BackupOutcome,
    BackupResult,
    RestoreOutcome,
    RestoreResult,
)
from iac_node_forge.application.dto import (
    OperationSummary,
    ProvisionOutcome,
    ProvisionResult,
    RemovalOutcome,
    RemovalResult,
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
from iac_node_forge.presentation.cli.commands._full_backup import (
    build_back_up_server,
    build_restore_server,
    kind_note,
)
from iac_node_forge.presentation.cli.commands._provisioning import (
    build_provision_server,
)
from iac_node_forge.presentation.cli.commands._removal import build_remove_server
from iac_node_forge.presentation.cli.operation_output import write_operation
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.provider_resolver import CliProviderResolver
from iac_node_forge.presentation.cli.services import CliServices

_NOUNS = {PowerAction.REMOVE: "removal"}
"""The noun for an action whose verb does not read well after ``the``."""


@dataclass(frozen=True, slots=True)
class Completion:
    """How the completion of an operation ended, as the commands show it.

    ``recorded`` is true when the end of the operation was recorded (a managed
    server, an ended managed record); ``lines`` are then printed. Otherwise
    ``reason`` says what is unfinished, labelled ``label``, and ``state_left``
    what the provider holds.
    """

    recorded: bool
    label: str
    lines: tuple[str, ...] = ()
    reason: str = ""
    state_left: str | None = None


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

    A provision or removal operation that has ended is also completed: the
    server is recorded as managed or its record is ended, or the state left at
    the provider is reported.
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
    completion = complete_operation(services, resolver, operation)
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


def complete_operation(
    services: CliServices, resolver: CliProviderResolver, operation: OperationSummary
) -> Completion | None:
    """Complete an operation that has ended: a provision, a removal, a backup or
    a restore; None for any other action."""
    if operation.state is OperationState.RUNNING:
        return None
    if operation.action is PowerAction.PROVISION:
        return _provisioned(
            build_provision_server(services, resolver).complete_provisioning(
                operation.operation_id
            )
        )
    if operation.action is PowerAction.REMOVE:
        return _removed(
            build_remove_server(services, resolver).complete_removal(
                operation.operation_id
            ),
            operation,
        )
    if operation.action is PowerAction.BACKUP:
        return _backed_up(
            build_back_up_server(services, resolver).complete_backup(
                operation.operation_id
            )
        )
    if operation.action is PowerAction.RESTORE:
        return _restored(
            build_restore_server(services, resolver).complete_restore(
                operation.operation_id
            ),
            operation,
        )
    return None


def write_completion(stream: TextIO, completion: Completion) -> None:
    """Say how the completion of an operation ended, after the operation block."""
    if completion.recorded:
        for line in completion.lines:
            write_line(stream, line)
        return
    if completion.reason:
        write_line(stream, f"{completion.label}: {completion.reason}")
    if completion.state_left:
        write_line(stream, f"State left at the provider: {completion.state_left}")


def _provisioned(result: ProvisionResult) -> Completion:
    if result.outcome is ProvisionOutcome.COMPLETED:
        return Completion(
            True,
            "Provisioning",
            (
                f"Managed: server {result.server_identifier!r} is recorded as "
                "managed by this client.",
            ),
        )
    return Completion(False, "Provisioning", (), result.reason, result.state_left)


def _removed(result: RemovalResult, operation: OperationSummary) -> Completion:
    if result.outcome is not RemovalOutcome.COMPLETED:
        return Completion(False, "Removal", (), result.reason, result.state_left)
    server = repr(operation.server_identifier)
    lines = [f"State left at the provider: {result.state_left}"]
    if result.managed_ended:
        lines.append(
            f"Managed: the record of server {server} is ended; it no longer "
            "counts as managed."
        )
    else:
        lines.append(
            f"Managed: server {server} was not managed by this client; no "
            "record was changed."
        )
    return Completion(True, "Removal", tuple(lines))


def _backed_up(result: BackupResult) -> Completion:
    backup = result.backup
    if result.outcome is not BackupOutcome.COMPLETED or backup is None:
        return Completion(False, "Backup", (), result.reason, result.state_left)
    lines = [
        f"Kept at: {backup.location}",
        f"Backup: {backup.backup_id} (alias {backup.alias}), {backup.kind.value}, "
        f"made {backup.created_at.isoformat()}; recorded by this client.",
    ]
    if backup.auto_delete_at is not None:
        lines.append(f"The provider deletes it on {backup.auto_delete_at.isoformat()}.")
    note = kind_note(backup.kind)
    if note is not None:
        lines.append(note)
    return Completion(True, "Backup", tuple(lines))


def _restored(result: RestoreResult, operation: OperationSummary) -> Completion:
    held = (
        None
        if result.state_after is None
        else f"server {operation.server_identifier!r} is {result.state_after}"
    )
    if result.outcome is not RestoreOutcome.COMPLETED:
        return Completion(False, "Restore", (), result.reason, held)
    if held is None:
        return Completion(
            True, "Restore", (f"State after the restore: {result.reason}",)
        )
    return Completion(
        True,
        "Restore",
        (
            f"State after the restore: {held}.",
            "Recorded: the state is kept in the detail of the operation.",
        ),
    )


def _is_recorded(completion: Completion | None) -> bool:
    """Whether the end counts as a success: nothing to complete, or a recorded one."""
    return completion is None or completion.recorded


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
    completion: Completion | None = None,
) -> None:
    action = _NOUNS.get(operation.action, operation.action.value)
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
    if completion is not None and completion.state_left:
        write_line(err, f"State left at the provider: {completion.state_left}")
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
