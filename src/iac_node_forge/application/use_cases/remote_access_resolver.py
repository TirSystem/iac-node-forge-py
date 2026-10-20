"""The checks both transfers make before the server is read (ADR-0009)."""

from iac_node_forge.application.errors import ServerNotFoundError, TransferError
from iac_node_forge.application.ports import (
    ConfigurationStore,
    KnownHostsPort,
    ProviderResolver,
    RemoteReadPort,
    ServerAccessStore,
)
from iac_node_forge.application.transfer_dto import AccessResolution, TransferOutcome
from iac_node_forge.application.use_cases._configuration_lookup import (
    find_configuration,
)
from iac_node_forge.application.use_cases._transfer_support import outcome_of
from iac_node_forge.domain.errors import DomainError
from iac_node_forge.domain.host_key import HostKey, HostKeyStanding
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_access import RemoteAccess, ServerAccess

RUNNING = "running"
_ACCESS_HINT = (
    "define it with 'iac-node-forge server access <configuration> <identifier> "
    "--user <user>'"
)


class RemoteAccessResolver:
    """Checks, once for backup and restore, that a server can be read.

    The order is fixed: the server at the provider and its state, the recorded
    access and an address, then the host key. Nothing is read from the server
    but its host key until that is known; a key shown for the first time is
    written to ``known_hosts`` only when the caller passes its exact
    fingerprint, and a changed key is never accepted.
    """

    def __init__(
        self,
        providers: ProviderResolver,
        configurations: ConfigurationStore,
        accesses: ServerAccessStore,
        reader: RemoteReadPort,
        known_hosts: KnownHostsPort,
    ) -> None:
        self._providers = providers
        self._configurations = configurations
        self._accesses = accesses
        self._reader = reader
        self._known_hosts = known_hosts

    def resolve(
        self,
        configuration: str,
        server_identifier: str,
        accepted_fingerprint: str | None,
    ) -> AccessResolution:
        """Return READY with the access and key, or the first reason it is not.

        Raises the use case and provider errors of the reads (configuration not
        found, provider unreachable, credentials rejected).
        """
        target = find_configuration(self._configurations, configuration)
        server = self._server(target, server_identifier)
        if isinstance(server, AccessResolution):
            return server
        managed = self._is_managed(target, server)
        if server.state != RUNNING:
            return AccessResolution(
                TransferOutcome.SERVER_NOT_RUNNING,
                f"the server is {server.state}; it must be running to be reached",
                target,
                server,
                managed,
            )
        found = self._access(target, server, managed)
        if isinstance(found, AccessResolution):
            return found
        return self._check_host_key(
            target, server, managed, found, accepted_fingerprint
        )

    def _server(
        self, target: ProviderConfiguration, server_identifier: str
    ) -> Server | AccessResolution:
        provider = self._providers.provider_for(target)
        try:
            return provider.get_server(target, server_identifier)
        except ServerNotFoundError:
            return AccessResolution(
                TransferOutcome.SERVER_NOT_FOUND,
                f"the provider has no server {server_identifier!r}",
                target,
            )

    def _is_managed(self, target: ProviderConfiguration, server: Server) -> bool:
        managed_ids = {
            record.server_identifier
            for record in self._configurations.managed_servers(target.configuration_id)
        }
        return server.managed or server.server_identifier in managed_ids

    def _access(
        self, target: ProviderConfiguration, server: Server, managed: bool
    ) -> RemoteAccess | AccessResolution:
        recorded = self._accesses.load(
            target.configuration_id, server.server_identifier
        )

        def missing(reason: str) -> AccessResolution:
            return AccessResolution(
                TransferOutcome.ACCESS_MISSING, reason, target, server, managed
            )

        if recorded is None:
            return missing(f"no access is recorded for the server; {_ACCESS_HINT}")
        host = _host_of(recorded, server)
        if host is None:
            return missing(
                "no address is known for the server: the record has no host and "
                f"the provider reports none; {_ACCESS_HINT} with --host"
            )
        try:
            return RemoteAccess(
                host, recorded.port, recorded.user, recorded.identity_hint
            )
        except DomainError as error:
            return missing(f"the recorded access cannot be used: {error}")

    def _check_host_key(
        self,
        target: ProviderConfiguration,
        server: Server,
        managed: bool,
        access: RemoteAccess,
        accepted_fingerprint: str | None,
    ) -> AccessResolution:
        def stop(
            outcome: TransferOutcome, reason: str, key: HostKey | None = None
        ) -> AccessResolution:
            return AccessResolution(
                outcome, reason, target, server, managed, access, key
            )

        try:
            check = self._reader.check_host_key(access)
            key = check.host_key
            if check.standing is HostKeyStanding.KNOWN:
                return stop(TransferOutcome.READY, "", key)
            if key is None:
                return stop(
                    TransferOutcome.CONNECTION_FAILED,
                    "the server showed no host key",
                )
            if check.standing is HostKeyStanding.CHANGED:
                return stop(
                    TransferOutcome.HOST_KEY_CHANGED,
                    f"the host key of {access.host} differs from the one in "
                    "known_hosts; it is never accepted here",
                    key,
                )
            if accepted_fingerprint == key.fingerprint:
                self._known_hosts.trust(key)
                return stop(TransferOutcome.READY, "", key)
            return stop(
                TransferOutcome.HOST_KEY_UNKNOWN,
                _unknown_reason(access, key, accepted_fingerprint),
                key,
            )
        except TransferError as error:
            outcome, reason = outcome_of(error)
            return stop(outcome, reason)


def _host_of(recorded: ServerAccess, server: Server) -> str | None:
    """Return the recorded host, else the first address the provider reports."""
    if recorded.host is not None:
        return recorded.host
    for address in server.addresses:
        host = address.split("/", 1)[0].strip()
        if host:
            return host
    return None


def _unknown_reason(
    access: RemoteAccess, key: HostKey, accepted_fingerprint: str | None
) -> str:
    if accepted_fingerprint is None:
        return (
            f"the host key of {access.host} is not in known_hosts yet "
            f"({key.key_type} {key.fingerprint})"
        )
    return (
        f"the fingerprint given does not match the key {access.host} shows now "
        f"({key.key_type} {key.fingerprint}); known_hosts was not changed"
    )
