"""The boundary to asking the user for a secret."""

from typing import Protocol, runtime_checkable

from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_kind import ProviderKind


@runtime_checkable
class CredentialPrompt(Protocol):
    """Asks the user for the secret of a provider."""

    def ask(self, provider_kind: ProviderKind) -> Credentials:
        """Ask the user for credentials for the given provider."""
        ...
