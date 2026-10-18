"""A named, reusable description of the server wanted."""

from dataclasses import dataclass

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.errors import (
    BootstrapHintTooLongError,
    DuplicateValueNameError,
)
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.provider_value import ProviderValue

MAX_BOOTSTRAP_HINT_LENGTH = 500
"""Longest ``bootstrap_hint`` a DesiredConfiguration accepts, in characters."""


def require_unique_names(owner: str, values: tuple[ProviderValue, ...]) -> None:
    """Raise DuplicateValueNameError when two values share a name."""
    names = [value.name for value in values]
    if len(set(names)) != len(names):
        raise DuplicateValueNameError(f"{owner}.values repeats a value name")


@dataclass(frozen=True, slots=True)
class DesiredConfiguration:
    """The neutral core and the provider values of a server to create.

    It is a template: each provisioning creates one server from it. It holds
    the names of the access keys to install, never a key value, and no secret.
    """

    configuration_id: ConfigurationId
    desired_name: str
    image: str
    access_key_names: tuple[str, ...] = ()
    bootstrap_hint: str | None = None
    values: tuple[ProviderValue, ...] = ()

    def __post_init__(self) -> None:
        require_text("DesiredConfiguration", "configuration_id", self.configuration_id)
        require_text("DesiredConfiguration", "desired_name", self.desired_name)
        require_text("DesiredConfiguration", "image", self.image)
        for key_name in self.access_key_names:
            require_text("DesiredConfiguration", "access_key_names", key_name)
        if self.bootstrap_hint is not None:
            require_text("DesiredConfiguration", "bootstrap_hint", self.bootstrap_hint)
            if len(self.bootstrap_hint) > MAX_BOOTSTRAP_HINT_LENGTH:
                raise BootstrapHintTooLongError(
                    f"DesiredConfiguration.bootstrap_hint has "
                    f"{len(self.bootstrap_hint)} characters; the limit is "
                    f"{MAX_BOOTSTRAP_HINT_LENGTH}"
                )
        require_unique_names("DesiredConfiguration", self.values)

    def value(self, name: str) -> str | None:
        """Return the provider value called ``name``, or None when absent."""
        return next((v.value for v in self.values if v.name == name), None)
