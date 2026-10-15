"""Finding a saved configuration by identifier or display name."""

from iac_node_forge.application.errors import (
    AmbiguousConfigurationError,
    ConfigurationNotFoundError,
    NoConfigurationError,
)
from iac_node_forge.application.ports import ConfigurationStore
from iac_node_forge.domain.provider_configuration import ProviderConfiguration


def find_configuration(store: ConfigurationStore, key: str) -> ProviderConfiguration:
    """Return the configuration whose identifier or display name is ``key``.

    An identifier match wins; a display name is matched ignoring case.
    """
    configurations = store.load_all()
    if not configurations:
        raise NoConfigurationError("no provider is configured")
    for configuration in configurations:
        if configuration.configuration_id == key:
            return configuration
    wanted = key.strip().casefold()
    named = [c for c in configurations if c.display_name.strip().casefold() == wanted]
    if not named:
        raise ConfigurationNotFoundError(f"no provider configuration {key!r}")
    if len(named) > 1:
        raise AmbiguousConfigurationError(
            f"{len(named)} provider configurations are named {key!r}; use the id"
        )
    return named[0]
