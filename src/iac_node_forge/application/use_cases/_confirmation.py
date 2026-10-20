"""The typed confirmation a removal and a restore both need."""

from dataclasses import dataclass

from iac_node_forge.application.dto import Confirmation


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why a confirmation is not enough; ``mismatch`` is a wrong identifier."""

    mismatch: bool
    reason: str


def confirmation_refusal(
    confirmation: Confirmation, server_identifier: str, managed: bool, action: str
) -> Refusal | None:
    """Return the refusal when the confirmation is not complete and exact.

    The identifier must be typed and equal the provider's exactly; a server this
    client does not manage needs it typed once more. ``action`` is the noun the
    texts use, such as ``removal``.
    """
    if not confirmation.given:
        return Refusal(False, f"the {action} was not confirmed")
    if confirmation.typed_identifier is None:
        return Refusal(
            False, f"the server identifier must be typed to confirm the {action}"
        )
    if confirmation.typed_identifier != server_identifier:
        return Refusal(
            True, "the typed identifier does not match the server identifier"
        )
    if managed:
        return None
    if confirmation.unmanaged_identifier is None:
        return Refusal(
            False,
            "the server is not managed by this client; its identifier must be "
            "typed once more to confirm",
        )
    if confirmation.unmanaged_identifier != server_identifier:
        return Refusal(
            True, "the identifier typed again does not match the server identifier"
        )
    return None
