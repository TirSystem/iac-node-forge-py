"""What a provider declares about one provider-specific value."""

from dataclasses import dataclass

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.errors import InvalidValueRuleError


@dataclass(frozen=True, slots=True)
class ValueRule:
    """Declares one value a provider needs or accepts, and how it is checked.

    A value is checked in one of three ways: against the options of
    ``option_kind``, against the ``allowed`` values, or as a whole number
    within ``minimum`` and ``maximum``.
    """

    name: str
    required: bool
    option_kind: str | None = None
    allowed: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None

    def __post_init__(self) -> None:
        require_text("ValueRule", "name", self.name)
        if self.option_kind is not None:
            require_text("ValueRule", "option_kind", self.option_kind)
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise InvalidValueRuleError(
                f"ValueRule {self.name!r}: minimum is above maximum"
            )
