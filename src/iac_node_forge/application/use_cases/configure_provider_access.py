"""UC-001: validate access to a provider and save its configuration (OC-001)."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace

from iac_node_forge.application.dto import AccessCheck, SavedConfiguration
from iac_node_forge.application.errors import (
    AccessNotValidatedError,
    CredentialStoreUnavailableError,
    DuplicateDisplayNameError,
)
from iac_node_forge.application.ports import (
    ConfigurationStore,
    CredentialStore,
    ProviderPort,
)
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind

CREDENTIAL_STORE_KIND = "keyring"


def new_configuration_identifier() -> str:
    """Return a fresh identifier for a configuration."""
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True, repr=False)
class _ValidatedAccess:
    provider_kind: ProviderKind
    address: str
    credentials: Credentials

    def __repr__(self) -> str:
        return f"_ValidatedAccess({self.provider_kind}, {self.address!r})"


class ConfigureProviderAccess:
    """Checks access with the provider, then saves the configuration.

    The credentials that passed the check are held in memory until the
    configuration is saved (or another check replaces them), because the
    operation contract lets the caller ask to cache them without passing them
    again. They are never written to the configuration store.
    """

    def __init__(
        self,
        provider: ProviderPort,
        configurations: ConfigurationStore,
        credential_store: CredentialStore,
        new_identifier: Callable[[], str] = new_configuration_identifier,
    ) -> None:
        self._provider = provider
        self._configurations = configurations
        self._credential_store = credential_store
        self._new_identifier = new_identifier
        self._validated: _ValidatedAccess | None = None

    def validate_access(
        self, provider_kind: ProviderKind, address: str, credentials: Credentials
    ) -> AccessCheck:
        """Check that the provider accepts the credentials at the address."""
        self._validated = None
        result = self._provider.validate_access(address, credentials)
        if result.succeeded:
            self._validated = _ValidatedAccess(provider_kind, address, credentials)
        return AccessCheck(result.succeeded, result.reason)

    def save_provider_configuration(
        self,
        provider_kind: ProviderKind,
        address: str,
        display_name: str,
        cache_credential: bool,
    ) -> SavedConfiguration:
        """Save a configuration for access validated in this session.

        Raises AccessNotValidatedError when no successful check matches the
        kind and address, and DuplicateDisplayNameError when the name is taken;
        nothing is created then. When the platform credential store is
        unavailable the configuration is saved without a credential reference
        and the result says so.
        """
        validated = self._validated
        if (
            validated is None
            or validated.provider_kind is not provider_kind
            or validated.address != address
        ):
            raise AccessNotValidatedError(
                "access to this provider was not validated in this session"
            )
        if self._name_is_taken(display_name):
            raise DuplicateDisplayNameError(
                f"a provider configuration named {display_name!r} already exists"
            )
        configuration = ProviderConfiguration(
            ConfigurationId(self._new_identifier()),
            provider_kind,
            display_name,
            address,
        )
        reference = self._cache(configuration, validated.credentials, cache_credential)
        if reference is not None:
            configuration = replace(configuration, credential_reference=reference)
        self._configurations.save(configuration)
        self._validated = None
        return SavedConfiguration(
            configuration.configuration_id,
            display_name,
            credential_cached=reference is not None,
            credential_cache_unavailable=cache_credential and reference is None,
        )

    def _name_is_taken(self, display_name: str) -> bool:
        wanted = display_name.strip().casefold()
        return any(
            c.display_name.strip().casefold() == wanted
            for c in self._configurations.load_all()
        )

    def _cache(
        self,
        configuration: ProviderConfiguration,
        credentials: Credentials,
        cache_credential: bool,
    ) -> CredentialReference | None:
        if not cache_credential:
            return None
        reference = CredentialReference(
            f"{configuration.provider_kind.value}-{configuration.configuration_id}",
            CREDENTIAL_STORE_KIND,
        )
        try:
            self._credential_store.put(reference, credentials)
        except CredentialStoreUnavailableError:
            return None
        return reference
