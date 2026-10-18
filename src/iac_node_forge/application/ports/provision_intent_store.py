"""The boundary to the intents saved before a create request."""

from typing import Protocol, runtime_checkable

from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.provision_intent import IntentId, ProvisionIntent


@runtime_checkable
class ProvisionIntentStore(Protocol):
    """Keeps provision intents, apart from the operations (ADR-0007)."""

    def save(self, intent: ProvisionIntent) -> None:
        """Persist an intent, replacing the one with the same identifier."""
        ...

    def load(self, intent_id: IntentId) -> ProvisionIntent:
        """Return the intent with this identifier.

        Raises ProvisionIntentNotFoundError when none is recorded.
        """
        ...

    def find_pending(
        self, configuration_id: ConfigurationId, server_name: str
    ) -> ProvisionIntent | None:
        """Return the pending intent for a server name, or None."""
        ...

    def load_by_operation(self, operation_id: OperationId) -> ProvisionIntent | None:
        """Return the intent linked to an operation, or None."""
        ...
