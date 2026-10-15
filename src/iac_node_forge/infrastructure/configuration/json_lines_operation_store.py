"""OperationStore kept in the client's JSON Lines file (ERD-001 OperationRecord).

The records live in the same file as the configuration, as ``operation``
lines, and the configuration records are written back unchanged. An
Operation has no field that can hold a secret and its texts are bounded by
the domain, so nothing else is filtered here.
"""

from pathlib import Path

from iac_node_forge.application.errors import OperationNotFoundError
from iac_node_forge.domain.operation import Operation, OperationId
from iac_node_forge.infrastructure.configuration._json_lines_file import (
    read_snapshot,
    write_snapshot,
)


class JsonLinesOperationStore:
    """Keeps operations as records of the client's JSON Lines file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, operation: Operation) -> None:
        """Insert the operation, or replace the one with the same identifier."""
        snapshot = read_snapshot(self._path)
        snapshot.operations[operation.operation_id] = operation
        write_snapshot(self._path, snapshot)

    def load(self, operation_id: OperationId) -> Operation:
        """Return the operation; raises OperationNotFoundError when absent."""
        operation = read_snapshot(self._path).operations.get(operation_id)
        if operation is None:
            raise OperationNotFoundError(str(operation_id))
        return operation
