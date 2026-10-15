"""CredentialStore on the operating system keyring, through ``keyring``.

Windows uses Credential Manager, macOS the Keychain, Linux the Secret Service.
The service name is ``iac-node-forge`` and the user name is the credential
reference name, so the JSONL never holds more than that name (ADR-0003).

Not implemented: the Windows Hello check. A prompt from Python needs WinRT
bindings that have not been verified, so a stored secret is used without one.
"""

import keyring
from keyring.errors import KeyringError

from iac_node_forge.application.errors import (
    CredentialNotFoundError,
    CredentialStoreUnavailableError,
)
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials

SERVICE_NAME = "iac-node-forge"
_UNAVAILABLE = "no usable platform credential store; ask for the secret each time"


class PlatformCredentialStore:
    """Keeps secrets in the platform keyring, one entry per reference name."""

    def put(self, reference: CredentialReference, credentials: Credentials) -> None:
        """Store the secret; raises CredentialStoreUnavailableError if no keyring."""
        try:
            keyring.set_password(SERVICE_NAME, reference.name, credentials.secret)
        except KeyringError:
            # The original error is dropped: it may echo what was passed in.
            raise CredentialStoreUnavailableError(_UNAVAILABLE) from None

    def get(self, reference: CredentialReference) -> Credentials:
        """Return the secret; raises CredentialNotFoundError when none is kept."""
        try:
            secret = keyring.get_password(SERVICE_NAME, reference.name)
        except KeyringError:
            raise CredentialStoreUnavailableError(_UNAVAILABLE) from None
        if not secret:
            raise CredentialNotFoundError(
                f"no secret kept under reference {reference.name!r}"
            )
        return Credentials(secret)
