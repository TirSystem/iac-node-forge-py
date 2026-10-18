"""How to reach a server over SSH: what is kept and what one run uses (ADR-0009)."""

import re
from dataclasses import dataclass

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.errors import InvalidServerAccessError
from iac_node_forge.domain.provider_configuration import ConfigurationId

DEFAULT_PORT = 22
MAX_PORT = 65535
MAX_IDENTITY_HINT_LENGTH = 500

_HOST = re.compile(r"[\w.:%-]+")
_USER = re.compile(r"[\w.@+\\-]+")
_KEY_MARKERS = ("PRIVATE KEY", "-----BEGIN")


def validate_host(owner: str, host: str) -> str:
    """Return ``host`` when it is a name or an address ssh can take as is.

    No space or other character that ssh or a shell could read as more than a
    name, and no leading ``-`` that ssh would read as an option.
    """
    if host.startswith("-") or _HOST.fullmatch(host) is None:
        raise InvalidServerAccessError(
            f"{owner}.host must be a name or an address without spaces "
            "and without a leading '-'"
        )
    return host


def validate_port(owner: str, port: int) -> int:
    """Return ``port`` when it is a TCP port from 1 to 65535."""
    if isinstance(port, bool) or not 1 <= port <= MAX_PORT:
        raise InvalidServerAccessError(f"{owner}.port must be from 1 to {MAX_PORT}")
    return port


def validate_user(owner: str, user: str) -> str:
    """Return ``user`` when it is a user name; there is no default user."""
    require_text(owner, "user", user)
    if user.startswith("-") or _USER.fullmatch(user) is None:
        raise InvalidServerAccessError(
            f"{owner}.user must be a user name without spaces and without a leading '-'"
        )
    return user


def validate_identity_hint(owner: str, hint: str) -> str:
    """Return ``hint`` when it is short free text that is not key material."""
    if len(hint) > MAX_IDENTITY_HINT_LENGTH:
        raise InvalidServerAccessError(
            f"{owner}.identity_hint is longer than {MAX_IDENTITY_HINT_LENGTH}"
        )
    if any(ord(character) < 32 for character in hint) or any(
        marker in hint.upper() for marker in _KEY_MARKERS
    ):
        # The value is not repeated: it could be the key itself.
        raise InvalidServerAccessError(
            f"{owner}.identity_hint must be one line and not key material"
        )
    return hint


@dataclass(frozen=True, slots=True)
class ServerAccess:
    """The connection facts kept for one server of one configuration.

    ``host`` is empty when the address the provider reports is to be used at
    the time of the transfer. ``identity_hint`` is free text: a key file path
    or a note, never a key. There is no field for a password, a passphrase, a
    key or a token.
    """

    configuration_id: ConfigurationId
    server_identifier: str
    host: str | None
    port: int
    user: str
    identity_hint: str | None

    def __post_init__(self) -> None:
        require_text("ServerAccess", "configuration_id", self.configuration_id)
        require_text("ServerAccess", "server_identifier", self.server_identifier)
        if self.host is not None:
            validate_host("ServerAccess", self.host)
        validate_port("ServerAccess", self.port)
        validate_user("ServerAccess", self.user)
        if self.identity_hint is not None:
            validate_identity_hint("ServerAccess", self.identity_hint)


@dataclass(frozen=True, slots=True)
class RemoteAccess:
    """What one run uses to connect: the host is resolved."""

    host: str
    port: int
    user: str
    identity_hint: str | None

    def __post_init__(self) -> None:
        validate_host("RemoteAccess", self.host)
        validate_port("RemoteAccess", self.port)
        validate_user("RemoteAccess", self.user)
        if self.identity_hint is not None:
            validate_identity_hint("RemoteAccess", self.identity_hint)
