"""HTTP knowledge shared by the provider adapters.

Only what is the same for every adapter lives here: how an address becomes a
base URL, how the TLS setting becomes an httpx argument, and how a transport
failure is reported. Nothing here knows a provider.
"""

import ssl

import httpx

from iac_node_forge.application.errors import (
    ProviderTimeoutError,
    ProviderUnreachableError,
)


def base_url(address: str) -> str:
    """Return the address as ``scheme://host[:port]``, assuming https."""
    text = address.strip().rstrip("/")
    return text if "://" in text else f"https://{text}"


def tls_verify(verify: bool | str) -> ssl.SSLContext | bool:
    """Turn ``True``, ``False`` or a CA bundle path into an httpx argument."""
    if isinstance(verify, str):
        return ssl.create_default_context(cafile=verify)
    return verify


def unreachable_error(
    error: httpx.TransportError, timeout: float
) -> ProviderUnreachableError:
    """Describe a timeout or a network or TLS failure, without any URL or header."""
    if isinstance(error, httpx.TimeoutException):
        return ProviderTimeoutError(
            f"the provider did not answer within {timeout} seconds"
        )
    return ProviderUnreachableError(
        f"the provider could not be reached ({type(error).__name__}); "
        "check the address and the TLS certificate"
    )
