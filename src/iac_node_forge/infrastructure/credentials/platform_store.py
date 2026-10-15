"""CredentialStore on the operating system keyring, through ``keyring``.

Windows uses Credential Manager, macOS the Keychain, Linux the Secret Service.
The service name is ``iac-node-forge`` and the user name is the credential
reference name, so the JSONL never holds more than that name (ADR-0003). The
named values of one reference are one entry, serialised as a JSON object
(ADR-0005); the entry is replaced whole. A plain string written by the first
version is read as the value ``token``.

Not implemented: the Windows Hello check. A prompt from Python needs WinRT
bindings that have not been verified, so a stored secret is used without one.
"""

import json

import keyring
from keyring.errors import KeyringError

from iac_node_forge.application.errors import (
    CredentialEntryInvalidError,
    CredentialNotFoundError,
    CredentialStoreUnavailableError,
)
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import SINGLE_VALUE_NAME, Credentials
from iac_node_forge.domain.errors import DomainError

SERVICE_NAME = "iac-node-forge"
_UNAVAILABLE = "no usable platform credential store; ask for the secret each time"


class PlatformCredentialStore:
    """Keeps secrets in the platform keyring, one entry per reference name."""

    def put(self, reference: CredentialReference, credentials: Credentials) -> None:
        """Store the values; raises CredentialStoreUnavailableError if no keyring."""
        entry = json.dumps(dict(credentials.values))
        try:
            keyring.set_password(SERVICE_NAME, reference.name, entry)
        except KeyringError:
            # The original error is dropped: it may echo what was passed in.
            raise CredentialStoreUnavailableError(_UNAVAILABLE) from None

    def get(self, reference: CredentialReference) -> Credentials:
        """Return the values; raises CredentialNotFoundError when none is kept."""
        try:
            entry = keyring.get_password(SERVICE_NAME, reference.name)
        except KeyringError:
            raise CredentialStoreUnavailableError(_UNAVAILABLE) from None
        if not entry:
            raise CredentialNotFoundError(
                f"no secret kept under reference {reference.name!r}"
            )
        return _parse(entry, reference)


def _parse(entry: str, reference: CredentialReference) -> Credentials:
    """Read an entry; its content never appears in an error."""
    invalid = CredentialEntryInvalidError(
        f"the entry under reference {reference.name!r} is not readable"
    )
    if not entry.lstrip().startswith("{"):
        # Written by the first version: one plain token string.
        return _build({SINGLE_VALUE_NAME: entry}, invalid)
    try:
        data = json.loads(entry)
    except ValueError:
        raise invalid from None
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise invalid
    return _build(data, invalid)


def _build(values: dict[str, str], invalid: CredentialEntryInvalidError) -> Credentials:
    try:
        return Credentials(values)
    except DomainError:
        raise invalid from None
