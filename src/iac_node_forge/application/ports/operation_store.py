"""The boundary to the record of power operations."""

from typing import Protocol, runtime_checkable

from iac_node_forge.domain.operation import Operation, OperationId


@runtime_checkable
class OperationStore(Protocol):
    """Keeps operations; an Operation never holds a secret."""

    def save(self, operation: Operation) -> None:
        """Persist an operation, replacing the one with the same identifier."""
        ...

    def load(self, operation_id: OperationId) -> Operation:
        """Return the operation with this identifier.

        Raises OperationNotFoundError when none is recorded.
        """
        ...
