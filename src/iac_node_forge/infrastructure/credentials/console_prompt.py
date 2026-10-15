"""CredentialPrompt that asks on the console without echo."""

import getpass
from collections.abc import Callable

from iac_node_forge.application.errors import EmptySecretError
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_kind import ProviderKind


class ConsoleCredentialPrompt:
    """Asks for each secret value of a provider with a non-echoing reader."""

    def __init__(self, read_secret: Callable[[str], str] = getpass.getpass) -> None:
        self._read_secret = read_secret

    def ask(self, provider_kind: ProviderKind) -> Credentials:
        """Ask for every value the provider declares; an empty answer is refused."""
        values: dict[str, str] = {}
        for name in provider_kind.credential_names:
            answer = self._read_secret(f"{name} for {provider_kind.value}: ")
            if not answer.strip():
                raise EmptySecretError(f"an empty value for {name!r} is not accepted")
            values[name] = answer
        return Credentials(values)
