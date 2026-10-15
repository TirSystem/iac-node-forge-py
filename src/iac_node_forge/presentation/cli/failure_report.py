"""Turns a failure the user can act on into a message for standard error."""

from iac_node_forge.application.errors import (
    ConfigurationFormatError,
    ConfigurationNotFoundError,
    CredentialStoreError,
    NoConfigurationError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderUnreachableError,
    UseCaseError,
)
from iac_node_forge.domain.errors import DomainError
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


def describe(error: Exception, configuration: str | None) -> str:
    """Return the text for standard error, without a stack trace.

    ``configuration`` is what the user typed to name the provider, if anything.
    The text never holds a secret: no error of the application carries one.
    """
    provider = f"provider {configuration!r}" if configuration else "the provider"
    if isinstance(error, ProviderUnreachableError):
        return (
            f"error: {provider} could not be reached: {error}\n"
            "Hint: check the address and the network; for a self-signed "
            f"certificate set {VERIFY_ENV_VAR} (see --help)."
        )
    if isinstance(error, ProviderAuthenticationError):
        return (
            f"error: {provider} rejected the token: {error}\n"
            "Hint: check the token and its permissions; "
            "run 'provider add' to save a new one."
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
    if isinstance(error, EOFError):
        return "error: input ended before the answer was given"
    if isinstance(error, ConfigurationFormatError):
        return f"error: the configuration file cannot be read: {error}"
    return f"error: {error}"
