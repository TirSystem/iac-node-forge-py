"""The boundary to a provider's creation of a server."""

from typing import Protocol, runtime_checkable

from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.operation import Operation
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.value_rule import ValueRule


@runtime_checkable
class ServerProvisionPort(Protocol):
    """The create request and what is needed around it (ADR-0007).

    A narrow port beside ProviderPort and ServerPowerPort. Only
    ``request_provision`` writes; the other methods are reads, so a caller can
    show the rules and the cost before anything is sent.
    """

    def value_rules(
        self, configuration: ProviderConfiguration
    ) -> tuple[ValueRule, ...]:
        """Return the provider-specific values the provider declares."""
        ...

    def reserve_identifier(self, configuration: ProviderConfiguration) -> str | None:
        """Return an identifier the provider will give the new server, if the
        caller chooses it (Proxmox VE), or None when the provider chooses."""
        ...

    def cost_consequence(
        self, configuration: ProviderConfiguration, desired: DesiredConfiguration
    ) -> str | None:
        """Return the text that states the commitment a creation makes, or None
        when the provider imposes none."""
        ...

    def request_provision(
        self,
        configuration: ProviderConfiguration,
        desired: DesiredConfiguration,
        server_name: str,
        reserved_identifier: str | None,
    ) -> Operation:
        """Send the one create request.

        Returns the Operation with action PROVISION in state RUNNING, with the
        server identifier set when the provider already names the server, or in
        state FAILED with the reason as its detail when the provider rejects or
        cannot take the request (authentication, permission, rate limit, 4xx,
        5xx, unreachable); the request is not repeated.

        Raises ProvisionUnsupportedError when the provider does not declare
        the capability ``provision_server`` (no request is sent).
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
