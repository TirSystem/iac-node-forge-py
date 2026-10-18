"""How an operation is shown, for the commands that start or show one."""

from typing import TextIO

from iac_node_forge.application.dto import OperationSummary
from iac_node_forge.presentation.cli.output import write_line

_LABEL_WIDTH = 15


def write_detail_line(stream: TextIO, label: str, value: str) -> None:
    """Write one ``Label:  value`` line of a detail block."""
    write_line(stream, f"{label + ':':<{_LABEL_WIDTH}}{value}")


def write_operation(
    stream: TextIO, operation: OperationSummary, server_state: str | None = None
) -> None:
    """Print the operation as labelled lines; the state is the word users read."""
    write_detail_line(stream, "Operation", operation.operation_id)
    write_detail_line(stream, "Configuration", operation.configuration_id)
    if operation.server_identifier is not None:
        write_detail_line(stream, "Server", operation.server_identifier)
    write_detail_line(stream, "Action", operation.action.value)
    write_detail_line(stream, "State", operation.state.value)
    write_detail_line(stream, "Started", operation.started_at.isoformat())
    if operation.finished_at is not None:
        write_detail_line(stream, "Finished", operation.finished_at.isoformat())
    if operation.provider_reference is not None:
        write_detail_line(stream, "Reference", operation.provider_reference)
    if operation.detail is not None:
        write_detail_line(stream, "Detail", operation.detail)
    if operation.consequence is not None:
        write_detail_line(stream, "Consequence", operation.consequence)
    if server_state is not None:
        write_detail_line(stream, "Server state", server_state)
