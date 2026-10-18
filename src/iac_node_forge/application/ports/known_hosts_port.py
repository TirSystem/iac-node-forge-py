"""The boundary to the one write to the user's own known_hosts."""

from typing import Protocol, runtime_checkable

from iac_node_forge.domain.host_key import HostKey


@runtime_checkable
class KnownHostsPort(Protocol):
    """Adds a host key the user accepted by its fingerprint."""

    def trust(self, host_key: HostKey) -> None:
        """Append the key of ``host_key`` to known_hosts; never rewrite it.

        The key is read from the server again and must have this fingerprint.
        Raises HostKeyUnknownError when the server now shows another key,
        HostKeyChangedError when the host already has an entry (a changed key is
        never accepted) and ConnectionFailedError when no key can be read.
        """
        ...
