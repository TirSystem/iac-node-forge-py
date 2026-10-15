"""The boundary to a provider's power actions."""

from typing import Protocol, runtime_checkable

from iac_node_forge.domain.operation import Operation
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration


@runtime_checkable
class ServerPowerPort(Protocol):
    """The four power requests and the reading of an operation (ADR-0006).

    A narrow port beside ProviderPort: code that only reads cannot reach a
    write by type, and the write surface is exactly four requests.
    """

    def request_power(
        self,
        configuration: ProviderConfiguration,
        server_identifier: str,
        action: PowerAction,
    ) -> Operation:
        """Send one power request for a server.

        Returns the Operation in state RUNNING, or in state FAILED with the
        reason as its detail when the provider rejects or cannot take the
        request (authentication, rate limit, 4xx, 5xx, unreachable); the
        request is not repeated.

        Raises PowerActionUnsupportedError when the provider does not declare
        the capability of ``action`` (no request is sent) and
        ServerNotFoundError when the provider has no such server.
        """
        ...

    def operation_state(
        self, configuration: ProviderConfiguration, operation: Operation
    ) -> Operation:
        """Return ``operation`` updated with the provider's current view.

        One read, no waiting: the state, the detail and, when it ended, the end
        time. An operation that has already ended is returned unchanged.
        Polling and the time limit belong to the caller.

        Raises a ProviderError subclass when the provider cannot be asked or
        no longer knows the server or the task (ServerNotFoundError for the
        server).
        """
        ...
