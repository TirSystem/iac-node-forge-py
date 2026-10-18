"""The boundary to a provider's removal of a server."""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from iac_node_forge.domain.operation import Operation
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.removal_kind import RemovalKind
from iac_node_forge.domain.server import Server


class RemovalBlock(StrEnum):
    """Why a server cannot be removed now, as the provider's port reports it."""

    RUNNING = "running"
    ALREADY_REMOVED = "already removed"


@runtime_checkable
class ServerRemovalPort(Protocol):
    """The removal request and what is needed around it (ADR-0008).

    A narrow port beside ProviderPort, ServerPowerPort and ServerProvisionPort.
    Only ``request_removal`` writes; the other methods are reads, so a caller
    can state the kind, refuse a blocked server and show the consequence before
    anything is sent.
    """

    def removal_kind(self, configuration: ProviderConfiguration) -> RemovalKind:
        """Return what a removal does at this provider: destroy or cancel."""
        ...

    def removal_block(
        self, configuration: ProviderConfiguration, server: Server
    ) -> RemovalBlock | None:
        """Return why ``server`` cannot be removed now, or None when it can.

        Reads the state of ``server`` only; it sends no request.
        """
        ...

    def consequence(self, configuration: ProviderConfiguration, server: Server) -> str:
        """Return the text that states what removing ``server`` does.

        Shown before the confirmation and recorded with the operation. It never
        holds a secret.
        """
        ...

    def request_removal(
        self, configuration: ProviderConfiguration, server: Server
    ) -> Operation:
        """Send the one removal request.

        Returns the Operation with action REMOVE in state RUNNING; or in state
        FAILED with the reason as its detail when the provider rejects the
        request (authentication, permission, rate limit, 4xx); or in state
        UNKNOWN when the answer was lost (timeout, 5xx) and the request may have
        reached the provider. The request is never repeated.

        Raises RemovalUnsupportedError when the provider does not declare the
        capability ``remove_server`` (no request is sent).
        """
        ...

    def operation_state(
        self, configuration: ProviderConfiguration, operation: Operation
    ) -> Operation:
        """Return ``operation`` updated with the provider's current view.

        One read, no waiting. An operation that has already ended is returned
        unchanged. Polling and the time limit belong to the caller.

        Raises a ProviderError subclass when the provider cannot be asked or
        no longer knows the task or the instance.
        """
        ...
