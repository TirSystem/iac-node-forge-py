"""UC-016: record how to reach a server (ADR-0009)."""

from iac_node_forge.application.errors import (
    ConfigurationNotFoundError,
    NoConfigurationError,
    ServerNotFoundError,
)
from iac_node_forge.application.ports import (
    ConfigurationStore,
    ProviderResolver,
    ServerAccessStore,
)
from iac_node_forge.application.transfer_dto import AccessOutcome, ServerAccessView
from iac_node_forge.application.use_cases._configuration_lookup import (
    find_configuration,
)
from iac_node_forge.domain.errors import DomainError
from iac_node_forge.domain.server_access import DEFAULT_PORT, ServerAccess


class DefineServerAccess:
    """Validates and keeps the host, port, user and identity hint of a server.

    Nothing is connected to and nothing but the read of the server is sent to
    the provider. There is no field for a secret.
    """

    def __init__(
        self,
        providers: ProviderResolver,
        configurations: ConfigurationStore,
        accesses: ServerAccessStore,
    ) -> None:
        self._providers = providers
        self._configurations = configurations
        self._accesses = accesses

    def define_server_access(
        self,
        configuration: str,
        server_identifier: str,
        host: str | None,
        port: int | None,
        user: str,
        identity_hint: str | None,
    ) -> ServerAccessView:
        """Keep the access of the server, replacing the one it had.

        The outcome is NOT_FOUND for an unknown configuration or server and
        INVALID, with the field that is wrong and never its value, for a user,
        host, port or hint that is not acceptable; nothing is kept then. Raises
        the provider errors of the read (unreachable, credentials rejected).
        """
        try:
            target = find_configuration(self._configurations, configuration)
        except (ConfigurationNotFoundError, NoConfigurationError) as error:
            return ServerAccessView(
                AccessOutcome.NOT_FOUND, str(error), None, server_identifier
            )
        try:
            access = ServerAccess(
                target.configuration_id,
                server_identifier,
                host,
                DEFAULT_PORT if port is None else port,
                user,
                identity_hint,
            )
        except DomainError as error:
            return ServerAccessView(
                AccessOutcome.INVALID,
                str(error),
                target.configuration_id,
                server_identifier,
            )
        provider = self._providers.provider_for(target)
        try:
            provider.get_server(target, server_identifier)
        except ServerNotFoundError:
            return ServerAccessView(
                AccessOutcome.NOT_FOUND,
                f"the provider has no server {server_identifier!r}",
                target.configuration_id,
                server_identifier,
            )
        self._accesses.save(access)
        return ServerAccessView(
            AccessOutcome.SAVED,
            "",
            access.configuration_id,
            access.server_identifier,
            access.host,
            access.port,
            access.user,
            access.identity_hint,
        )
