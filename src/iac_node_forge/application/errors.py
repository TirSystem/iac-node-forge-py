"""Errors a provider adapter raises across the ProviderPort boundary.

None of these messages ever carries a secret.
"""


class ProviderError(Exception):
    """Base class of every error a provider adapter raises."""


class ProviderAuthenticationError(ProviderError):
    """The provider rejected the credentials or the credentials lack permission."""


class ProviderUnreachableError(ProviderError):
    """The provider could not be reached: connection, TLS or timeout failure."""


class ProviderTimeoutError(ProviderUnreachableError):
    """The provider did not answer in time; a request may still have reached it."""


class ProviderRateLimitError(ProviderError):
    """The provider refused the request because a rate limit was reached."""


class ProviderServiceError(ProviderError):
    """The provider answered with a server-side failure (HTTP 5xx)."""


class ProviderPageLimitError(ProviderError):
    """A listing needed more pages than the adapter is willing to read."""


class ServerNotFoundError(ProviderError):
    """The provider has no server with the requested identifier."""


class PowerActionUnsupportedError(ProviderError):
    """The provider does not declare the capability of the power action."""


class ProvisionUnsupportedError(ProviderError):
    """The provider does not declare the capability to create a server."""


class RemovalUnsupportedError(ProviderError):
    """The provider does not declare the capability to remove a server."""


class NotAPowerActionError(Exception):
    """A power operation was asked for an action that is not a power action."""


class UnknownOptionKindError(ProviderError):
    """The provider does not offer options of the requested kind."""


class ConfigurationFormatError(Exception):
    """The configuration file holds a record the client cannot accept."""


class CredentialStoreError(Exception):
    """Base class of the credential store and prompt errors.

    None of these messages ever carries a secret.
    """


class CredentialStoreUnavailableError(CredentialStoreError):
    """No usable platform credential store exists; ask for the secret each time."""


class CredentialNotFoundError(CredentialStoreError):
    """The credential store holds no secret under the reference."""


class CredentialEntryInvalidError(CredentialStoreError):
    """The stored entry of a reference is not of the expected shape."""


class EmptySecretError(CredentialStoreError):
    """The user gave an empty answer when asked for a secret."""


class UnsupportedProviderError(ProviderError):
    """No adapter for the provider is available in this client yet."""


class UseCaseError(Exception):
    """Base class of the errors a use case raises for a failed precondition."""


class AccessNotValidatedError(UseCaseError):
    """The configuration was not preceded by a successful access check."""


class DuplicateDisplayNameError(UseCaseError):
    """A provider configuration with this display name already exists."""


class ConfigurationNotFoundError(UseCaseError):
    """No provider configuration matches the given identifier or name."""


class AmbiguousConfigurationError(UseCaseError):
    """More than one provider configuration matches the given name."""


class OperationNotFoundError(UseCaseError):
    """No recorded operation has the given identifier."""


class DesiredConfigurationNotFoundError(UseCaseError):
    """No desired configuration has the given name for the provider configuration."""


class ProvisionIntentNotFoundError(UseCaseError):
    """No recorded provision intent has the given identifier."""


class NoConfigurationError(UseCaseError):
    """No provider is configured yet."""
