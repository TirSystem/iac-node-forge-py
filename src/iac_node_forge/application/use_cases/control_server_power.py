"""UC-008: start, shut down, stop and restart a server (ADR-0006)."""

from dataclasses import dataclass, replace

from iac_node_forge.application.capabilities import POWER_ACTION_CAPABILITY
from iac_node_forge.application.dto import (
    Confirmation,
    OperationSummary,
    PowerActionResult,
    PowerOutcome,
)
from iac_node_forge.application.errors import (
    PowerActionUnsupportedError,
    ServerNotFoundError,
)
from iac_node_forge.application.ports import (
    ConfigurationStore,
    OperationStore,
    ProviderResolver,
)
from iac_node_forge.application.use_cases._configuration_lookup import (
    find_configuration,
)
from iac_node_forge.domain.operation import OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ProviderConfiguration

# The state text both providers report once the action has been done. RESTART
# has none: a running server is restarted into the state it is already in.
_STATE_AFTER: dict[PowerAction, str] = {
    PowerAction.START: "running",
    PowerAction.SHUTDOWN: "stopped",
    PowerAction.STOP: "stopped",
}


@dataclass(frozen=True, slots=True)
class _Checked:
    """The configuration of a request, and the answer when it stops there."""

    target: ProviderConfiguration
    result: PowerActionResult


class ControlServerPower:
    """Checks capability, state and confirmation, then sends one request.

    The write is the last step: every refusal is decided from reads and from
    the confirmation, so a refused request has sent nothing.
    """

    def __init__(
        self,
        providers: ProviderResolver,
        configurations: ConfigurationStore,
        operations: OperationStore,
    ) -> None:
        self._providers = providers
        self._configurations = configurations
        self._operations = operations

    def preview_power_action(
        self, configuration: str, server_identifier: str, action: PowerAction
    ) -> PowerActionResult:
        """Check everything but the confirmation; send and record nothing.

        The outcome is READY when the action can be asked for, so the client
        can state what will happen, or the reason it cannot.
        """
        return self._check(configuration, server_identifier, action).result

    def request_power_action(
        self,
        configuration: str,
        server_identifier: str,
        action: PowerAction,
        confirmation: Confirmation,
    ) -> PowerActionResult:
        """Send one power request when every precondition holds, and record it.

        The outcome says why nothing was sent: the server is not found, the
        provider does not support the action, the server is already in the
        state, or the confirmation is missing or does not match. A provider
        that rejects the request yields REJECTED with a FAILED operation, and
        the request is not repeated. Raises the use case and provider errors
        of the reads (configuration not found, unreachable, rejected
        credentials).
        """
        checked = self._check(configuration, server_identifier, action)
        ready = checked.result
        if ready.outcome is not PowerOutcome.READY:
            return ready
        refusal = _confirmation_refusal(ready, confirmation)
        if refusal is not None:
            return refusal
        return self._send(checked.target, ready)

    def _check(
        self, configuration: str, server_identifier: str, action: PowerAction
    ) -> _Checked:
        target = find_configuration(self._configurations, configuration)
        provider = self._providers.provider_for(target)
        base = PowerActionResult(
            PowerOutcome.NOT_FOUND,
            target.configuration_id,
            target.display_name,
            server_identifier,
            action,
            f"the provider has no server {server_identifier!r}",
        )
        try:
            server = provider.get_server(target, server_identifier)
        except ServerNotFoundError:
            return _Checked(target, base)
        managed_ids = {
            record.server_identifier
            for record in self._configurations.managed_servers(target.configuration_id)
        }
        known = replace(
            base,
            outcome=PowerOutcome.READY,
            reason="",
            server_name=server.name,
            server_state=server.state,
            managed=server.managed or server.server_identifier in managed_ids,
        )
        capability = POWER_ACTION_CAPABILITY[action]
        declared = {c.name for c in provider.discover_capabilities(target)}
        if capability not in declared:
            return _Checked(
                target,
                replace(
                    known,
                    outcome=PowerOutcome.UNSUPPORTED,
                    reason=f"the provider does not support {action.value} "
                    f"(capability {capability})",
                ),
            )
        if server.state.strip().casefold() == _STATE_AFTER.get(action):
            return _Checked(
                target,
                replace(
                    known,
                    outcome=PowerOutcome.ALREADY_IN_STATE,
                    reason=f"the server is already {server.state}",
                ),
            )
        return _Checked(target, known)

    def _send(
        self, target: ProviderConfiguration, ready: PowerActionResult
    ) -> PowerActionResult:
        power = self._providers.power_for(target)
        try:
            operation = power.request_power(
                target, ready.server_identifier, ready.action
            )
        except ServerNotFoundError:
            return replace(
                ready,
                outcome=PowerOutcome.NOT_FOUND,
                reason=f"the provider has no server {ready.server_identifier!r}",
            )
        except PowerActionUnsupportedError as error:
            return replace(ready, outcome=PowerOutcome.UNSUPPORTED, reason=str(error))
        self._operations.save(operation)
        failed = operation.state is OperationState.FAILED
        return replace(
            ready,
            outcome=PowerOutcome.REJECTED if failed else PowerOutcome.REQUESTED,
            reason=(operation.detail or "") if failed else "",
            operation=OperationSummary.of(operation),
        )


def _confirmation_refusal(
    ready: PowerActionResult, confirmation: Confirmation
) -> PowerActionResult | None:
    if not confirmation.given:
        return replace(
            ready,
            outcome=PowerOutcome.NOT_CONFIRMED,
            reason="the action was not confirmed",
        )
    if ready.managed:
        return None
    if confirmation.typed_identifier is None:
        return replace(
            ready,
            outcome=PowerOutcome.NOT_CONFIRMED,
            reason="the server is not managed by this client; its identifier "
            "must be typed to confirm",
        )
    if confirmation.typed_identifier != ready.server_identifier:
        return replace(
            ready,
            outcome=PowerOutcome.IDENTIFIER_MISMATCH,
            reason="the typed identifier does not match the server identifier",
        )
    return None
