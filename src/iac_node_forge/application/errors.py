"""Errors a provider adapter raises across the ProviderPort boundary.

None of these messages ever carries a secret.
"""


class ProviderError(Exception):
    """Base class of every error a provider adapter raises."""


class ProviderAuthenticationError(ProviderError):
    """The provider rejected the credentials or the credentials lack permission."""


class ProviderUnreachableError(ProviderError):
    """The provider could not be reached: connection, TLS or timeout failure."""


class ServerNotFoundError(ProviderError):
    """The provider has no server with the requested identifier."""


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


class EmptySecretError(CredentialStoreError):
    """The user gave an empty answer when asked for a secret."""
