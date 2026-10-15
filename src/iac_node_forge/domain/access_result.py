"""The outcome of an access check."""

from dataclasses import dataclass

from iac_node_forge.domain.errors import MissingReasonError


@dataclass(frozen=True, slots=True)
class AccessResult:
    """Carries the outcome of an access check; a failure states its reason."""

    succeeded: bool
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.succeeded and not self.reason.strip():
            raise MissingReasonError("A failed AccessResult needs a reason")
