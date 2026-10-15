"""The boundary to the platform credential store."""

from typing import Protocol, runtime_checkable

from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials


@runtime_checkable
class CredentialStore(Protocol):
    """Keeps secrets by reference."""

    def put(self, reference: CredentialReference, credentials: Credentials) -> None:
        """Store a secret under a reference."""
        ...

    def get(self, reference: CredentialReference) -> Credentials:
        """Return the secret kept under a reference."""
        ...
