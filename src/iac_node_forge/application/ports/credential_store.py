"""The boundary to the platform credential store."""

from typing import Protocol, runtime_checkable

from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials


@runtime_checkable
class CredentialStore(Protocol):
    """Keeps the secret values of one credential reference."""

    def put(self, reference: CredentialReference, credentials: Credentials) -> None:
        """Store all values of the credentials under a reference, replacing any."""
        ...

    def get(self, reference: CredentialReference) -> Credentials:
        """Return all values kept under a reference."""
        ...
