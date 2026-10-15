"""CredentialPrompt that asks on the console without echo."""

import getpass
from collections.abc import Callable

from iac_node_forge.application.errors import EmptySecretError
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_kind import ProviderKind


class ConsoleCredentialPrompt:
    """Asks for a secret with a non-echoing reader."""

    def __init__(self, read_secret: Callable[[str], str] = getpass.getpass) -> None:
        self._read_secret = read_secret

    def ask(self, provider_kind: ProviderKind) -> Credentials:
        """Ask for the secret of a provider; an empty answer is refused."""
        answer = self._read_secret(f"Secret for {provider_kind.value}: ")
        if not answer.strip():
            raise EmptySecretError("an empty secret is not accepted")
        return Credentials(answer)
