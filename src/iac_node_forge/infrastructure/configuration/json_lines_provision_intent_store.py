"""ProvisionIntentStore kept in the client's JSON Lines file (ERD-001).

The intents live in the same file as the configuration, as ``provision_intent``
lines with a child line per copied value; every other record is written back
unchanged. An intent never holds a secret.
"""

from pathlib import Path

from iac_node_forge.application.errors import ProvisionIntentNotFoundError
from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.provision_intent import (
    IntentId,
    IntentState,
    ProvisionIntent,
)
from iac_node_forge.infrastructure.configuration._json_lines_file import (
    read_snapshot,
    write_snapshot,
)


class JsonLinesProvisionIntentStore:
    """Keeps provision intents as records of the client's JSON Lines file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, intent: ProvisionIntent) -> None:
        """Insert the intent, or replace the one with the same identifier."""
        snapshot = read_snapshot(self._path)
        snapshot.intents[intent.intent_id] = intent
        write_snapshot(self._path, snapshot)

    def load(self, intent_id: IntentId) -> ProvisionIntent:
        """Return the intent; raises ProvisionIntentNotFoundError when absent."""
        intent = read_snapshot(self._path).intents.get(intent_id)
        if intent is None:
            raise ProvisionIntentNotFoundError(str(intent_id))
        return intent

    def find_pending(
        self, configuration_id: ConfigurationId, server_name: str
    ) -> ProvisionIntent | None:
        """Return the pending intent for the server name, or None."""
        return next(
            (
                intent
                for intent in read_snapshot(self._path).intents.values()
                if intent.state is IntentState.PENDING
                and intent.configuration_id == configuration_id
                and intent.server_name == server_name
            ),
            None,
        )

    def load_by_operation(self, operation_id: OperationId) -> ProvisionIntent | None:
        """Return the intent linked to the operation, or None."""
        return next(
            (
                intent
                for intent in read_snapshot(self._path).intents.values()
                if intent.operation_id == operation_id
            ),
            None,
        )
