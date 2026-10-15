"""UC-002: discover the capabilities and server options of a provider."""

from iac_node_forge.application.capabilities import KNOWN_ACTIONS
from iac_node_forge.application.dto import (
    CapabilityEntry,
    CapabilityReport,
    OptionEntry,
)
from iac_node_forge.application.ports import ConfigurationStore, ProviderResolver
from iac_node_forge.application.use_cases._configuration_lookup import (
    find_configuration,
)


class DiscoverCapabilities:
    """Reads what a configured provider supports and offers; changes nothing.

    ``configuration`` is the identifier or the display name of a saved
    configuration. Provider errors (unreachable, rejected, unknown option
    kind) propagate as ``ProviderError`` subclasses.
    """

    def __init__(
        self, providers: ProviderResolver, configurations: ConfigurationStore
    ) -> None:
        self._providers = providers
        self._configurations = configurations

    def discover_capabilities(self, configuration: str) -> CapabilityReport:
        """Return every known action, marked supported or unsupported.

        An action the provider declares that the product does not know is
        listed as supported after the known ones.
        """
        target = find_configuration(self._configurations, configuration)
        provider = self._providers.provider_for(target)
        declared = {c.name for c in provider.discover_capabilities(target)}
        entries = [CapabilityEntry(name, name in declared) for name in KNOWN_ACTIONS]
        entries.extend(
            CapabilityEntry(name, True)
            for name in sorted(declared - set(KNOWN_ACTIONS))
        )
        return CapabilityReport(
            target.configuration_id,
            target.display_name,
            target.provider_kind,
            tuple(entries),
        )

    def list_server_options(
        self, configuration: str, option_kind: str
    ) -> tuple[OptionEntry, ...]:
        """Return the options of one kind the provider offers."""
        target = find_configuration(self._configurations, configuration)
        provider = self._providers.provider_for(target)
        options = provider.list_server_options(target, option_kind)
        return tuple(OptionEntry(option.kind, option.value) for option in options)
