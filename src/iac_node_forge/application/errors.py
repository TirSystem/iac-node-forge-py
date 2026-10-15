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
