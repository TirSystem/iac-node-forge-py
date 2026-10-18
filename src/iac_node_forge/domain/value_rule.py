"""What a provider declares about one provider-specific value."""

from dataclasses import dataclass

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.errors import InvalidValueRuleError

IMAGE_RULE_NAMES = ("template", "image")
"""The names of the rule that says how the neutral image is checked.

The ``image`` of a desired configuration is the single source of the image: a
provider names the option kind it is checked against in its rule called
``template`` (Proxmox VE) or ``image`` (Contabo), and that rule is not a value.
"""


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
