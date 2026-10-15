"""Turns a failure the user can act on into a message for standard error."""

from iac_node_forge.application.errors import (
    ConfigurationFormatError,
    ConfigurationNotFoundError,
    CredentialStoreError,
    NoConfigurationError,
    OperationNotFoundError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderUnreachableError,
    UseCaseError,
)
from iac_node_forge.domain.errors import DomainError
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.presentation.cli.provider_words import words_for
from iac_node_forge.presentation.cli.settings import VERIFY_ENV_VAR, SettingsError

HANDLED_ERRORS: tuple[type[Exception], ...] = (
    UseCaseError,
    ProviderError,
    CredentialStoreError,
    ConfigurationFormatError,
    DomainError,
    SettingsError,
    EOFError,
)


def describe(
    error: Exception, configuration: str | None, kind: ProviderKind | None = None
) -> str:
    """Return the text for standard error, without a stack trace.

    ``configuration`` is what the user typed to name the provider, if anything,
    and ``kind`` the provider kind of that configuration when it is known. The
    text never holds a secret: no error of the application carries one.
    """
    provider = _provider_phrase(configuration, kind)
    if isinstance(error, ProviderUnreachableError):
        return (
            f"error: {provider} could not be reached: {error}\n"
            "Hint: check the address and the network; for a self-signed "
            f"certificate set {VERIFY_ENV_VAR} (see --help)."
        )
    if isinstance(error, ProviderAuthenticationError):
        credentials = words_for(kind).credentials if kind else "credentials"
        return (
            f"error: {provider} rejected the {credentials}: {error}\n"
            f"Hint: check the {credentials} and its permissions; "
            "run 'provider add' to save new ones."
        )
    if isinstance(error, NoConfigurationError):
        return (
            "error: no provider is configured\n"
            "Hint: add one with 'iac-node-forge provider add'."
        )
    if isinstance(error, ConfigurationNotFoundError):
        return (
            f"error: {error}\n"
            "Hint: use the display name or the id given to 'provider add'."
        )
    if isinstance(error, OperationNotFoundError):
        return (
            f"error: no operation {error} is recorded\n"
            "Hint: use the operation identifier a power command printed."
        )
    if isinstance(error, EOFError):
        return "error: input ended before the answer was given"
    if isinstance(error, ConfigurationFormatError):
        return f"error: the configuration file cannot be read: {error}"
    return f"error: {error}"


def _provider_phrase(configuration: str | None, kind: ProviderKind | None) -> str:
    label = f" ({words_for(kind).label})" if kind else ""
    if configuration:
        return f"provider {configuration!r}{label}"
    return f"the {words_for(kind).label} provider" if kind else "the provider"
