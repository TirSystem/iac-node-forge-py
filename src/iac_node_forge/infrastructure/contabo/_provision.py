"""What a Contabo instance creation is made of: its value rules and its body.

Pure functions, no network. The provider sends the one request this module
describes, ``POST /v1/compute/instances`` (ADR-0007).
"""

from collections.abc import Mapping

from iac_node_forge.application.errors import ProviderError
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.value_rule import ValueRule
from iac_node_forge.infrastructure import _power

VALUE_REGION = "region"
VALUE_IMAGE = "image"
"""The rule that names the option kind of the neutral image."""
VALUE_PRODUCT = "product"
VALUE_PERIOD = "period"
PERIODS_MONTHS = ("1", "12", "24")
"""The contract periods Contabo accepts (C5)."""

_NO_PERIOD = "a contract period of 1, 12 or 24 months, which is not chosen yet"


def value_rules() -> tuple[ValueRule, ...]:
    """Return the values an instance creation needs, all required.

    The rule ``image`` is the rule of the neutral image, not of a value.
    """
    return (
        ValueRule(VALUE_REGION, required=True, option_kind="region"),
        ValueRule(VALUE_IMAGE, required=True, option_kind="image"),
        ValueRule(VALUE_PRODUCT, required=True, option_kind="product"),
        ValueRule(VALUE_PERIOD, required=True, allowed=PERIODS_MONTHS),
    )


def cost_consequence(desired: DesiredConfiguration) -> str:
    """State the commitment a creation makes, naming the period in months."""
    period = desired.value(VALUE_PERIOD)
    unit = "month" if period == "1" else "months"
    named = f"a contract period of {period} {unit}" if period else _NO_PERIOD
    return (
        f"Creating this instance commits the account to {named}; "
        "the commitment is billed whether or not the instance is used."
    )


def build_body(
    desired: DesiredConfiguration, server_name: str
) -> Mapping[str, str | int]:
    """Return the JSON body of the creation.

    The image is the neutral image of the description. Raises ProviderError,
    naming the value, when one is missing or the period is not a number;
    nothing has been sent at that point.
    """
    period = _power.required_value(desired, VALUE_PERIOD)
    if period not in PERIODS_MONTHS:
        raise ProviderError(
            f"the value {VALUE_PERIOD!r} must be one of " + ", ".join(PERIODS_MONTHS)
        )
    return {
        "imageId": desired.image,
        "productId": _power.required_value(desired, VALUE_PRODUCT),
        "region": _power.required_value(desired, VALUE_REGION),
        "period": int(period),
        "displayName": server_name,
    }
