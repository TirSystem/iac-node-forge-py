"""The one check of a desired configuration, shared by define and provision."""

from iac_node_forge.application.capabilities import PROVISION_SERVER
from iac_node_forge.application.ports import ProviderResolver
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.value_rule import IMAGE_RULE_NAMES, ValueRule

MAX_CHOICES_SHOWN = 10
"""Most choices a problem names; a longer list is cut and ends with ``...``."""

_IMAGE = "image"


class DesiredConfigurationValidator:
    """Checks a desired configuration against what the provider declares now.

    The provider's value rules say which values it needs and how each is
    checked; the options it offers now say what is available. The neutral
    ``image`` is checked against the option kind of the rule named ``template``
    or ``image``, and is not required a second time as a value.
    """

    def __init__(self, providers: ProviderResolver) -> None:
        self._providers = providers

    def supports_provisioning(self, configuration: ProviderConfiguration) -> bool:
        """Whether the provider declares the capability ``provision_server``."""
        provider = self._providers.provider_for(configuration)
        declared = provider.discover_capabilities(configuration)
        return any(c.name == PROVISION_SERVER for c in declared)

    def check(
        self, configuration: ProviderConfiguration, desired: DesiredConfiguration
    ) -> list[str]:
        """Return each problem of ``desired``, or an empty list when it is valid.

        Reads the provider's rules and options only. A provider that cannot be
        read raises its ProviderError.
        """
        rules = self._providers.provision_for(configuration).value_rules(configuration)
        offered = _Options(self._providers, configuration)
        image_rule = next((r for r in rules if r.name in IMAGE_RULE_NAMES), None)
        problems: list[str] = []
        if image_rule is not None and image_rule.option_kind is not None:
            problems += offered.check(_IMAGE, desired.image, image_rule.option_kind)
        value_rules = [r for r in rules if r is not image_rule]
        problems += _unknown_names(desired, value_rules)
        for rule in value_rules:
            problems += _check_value(rule, desired.value(rule.name), offered)
        return problems


class _Options:
    """The options of the provider by kind, read once each."""

    def __init__(
        self, providers: ProviderResolver, configuration: ProviderConfiguration
    ) -> None:
        self._provider = providers.provider_for(configuration)
        self._configuration = configuration
        self._by_kind: dict[str, tuple[str, ...]] = {}

    def check(self, name: str, value: str, kind: str) -> list[str]:
        if kind not in self._by_kind:
            options = self._provider.list_server_options(self._configuration, kind)
            self._by_kind[kind] = tuple(option.value for option in options)
        choices = self._by_kind[kind]
        if value in choices:
            return []
        return [f"{_not_offered(name, value)}; {_choices(choices)}"]


def _unknown_names(desired: DesiredConfiguration, rules: list[ValueRule]) -> list[str]:
    known = {rule.name for rule in rules}
    problems = []
    for value in desired.values:
        if value.name in IMAGE_RULE_NAMES:
            problems.append(
                f"the value {value.name!r} is the image of the description; "
                "give it as the image"
            )
        elif value.name not in known:
            problems.append(
                f"unknown value {value.name!r}; the provider knows "
                + ", ".join(sorted(known))
            )
    return problems


def _check_value(rule: ValueRule, value: str | None, offered: _Options) -> list[str]:
    if value is None:
        return [f"the value {rule.name!r} is required"] if rule.required else []
    problems: list[str] = []
    if rule.option_kind is not None:
        problems += offered.check(rule.name, value, rule.option_kind)
    if rule.allowed and value not in rule.allowed:
        problems.append(f"{_not_offered(rule.name, value)}; {_choices(rule.allowed)}")
    if rule.minimum is not None or rule.maximum is not None:
        problems += _check_bounds(rule, value)
    return problems


def _check_bounds(rule: ValueRule, value: str) -> list[str]:
    try:
        number = int(value)
    except ValueError:
        return [f"the value {rule.name!r} must be a whole number, not {value!r}"]
    if rule.minimum is not None and number < rule.minimum:
        return [f"the value {rule.name!r} is {number}; the minimum is {rule.minimum}"]
    if rule.maximum is not None and number > rule.maximum:
        return [f"the value {rule.name!r} is {number}; the maximum is {rule.maximum}"]
    return []


def _not_offered(name: str, value: str) -> str:
    return f"the {name} {value!r} is not offered by the provider"


def _choices(choices: tuple[str, ...]) -> str:
    if not choices:
        return "the provider offers none"
    shown = ", ".join(choices[:MAX_CHOICES_SHOWN])
    more = ", ..." if len(choices) > MAX_CHOICES_SHOWN else ""
    return f"choices: {shown}{more}"
