"""The words the client uses for each provider and its credentials."""

from dataclasses import dataclass

from iac_node_forge.domain.provider_kind import ProviderKind


@dataclass(frozen=True, slots=True)
class ProviderWords:
    """How a provider is named to the user.

    ``credentials`` names what the provider is accessed with, in the
    provider's own terms; ``format_hint`` says how a value is written, if the
    provider has a fixed format.
    """

    label: str
    credentials: str
    format_hint: str = ""


_WORDS = {
    ProviderKind.PROXMOX_VE: ProviderWords(
        "Proxmox VE", "API token", "as USER@REALM!TOKENID=SECRET"
    ),
    ProviderKind.CONTABO: ProviderWords(
        "Contabo", "client ID, client secret, API user and API password"
    ),
}


def words_for(kind: ProviderKind) -> ProviderWords:
    """Return the words for a provider kind."""
    return _WORDS[kind]


def describe_kinds_credentials() -> str:
    """Return one sentence naming the credentials of every provider kind."""
    parts = []
    for words in _WORDS.values():
        hint = f" {words.format_hint}" if words.format_hint else ""
        parts.append(f"{words.label}: {words.credentials}{hint}")
    return "; ".join(parts)


def describe_option_kinds() -> str:
    """Return one sentence naming the option kinds of every provider kind."""
    return "; ".join(
        f"{words.label}: {', '.join(kind.option_kinds)}"
        for kind, words in _WORDS.items()
    )
