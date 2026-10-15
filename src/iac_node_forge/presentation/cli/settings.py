"""Settings the command-line client reads from the environment."""

from collections.abc import Mapping
from pathlib import Path

VERIFY_ENV_VAR = "IAC_NODE_FORGE_VERIFY"


class SettingsError(Exception):
    """An environment setting has a value the client cannot use."""


def read_tls_verification(environment: Mapping[str, str]) -> bool | str:
    """Return the TLS verification setting of provider connections.

    ``IAC_NODE_FORGE_VERIFY`` is unset or ``true`` (verify with the system
    CAs), ``false`` (do not verify; for a self-signed certificate), or the
    path of a CA bundle file.
    """
    value = environment.get(VERIFY_ENV_VAR, "").strip()
    if value == "" or value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if not Path(value).is_file():
        raise SettingsError(
            f"{VERIFY_ENV_VAR} must be true, false or the path of a CA bundle "
            f"file; {value!r} is not a file"
        )
    return value
