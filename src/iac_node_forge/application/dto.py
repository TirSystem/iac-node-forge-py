"""Plain results the use cases hand to presentation."""

from dataclasses import dataclass
from enum import Enum

from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.provider_kind import ProviderKind


@dataclass(frozen=True, slots=True)
class AccessCheck:
    """Whether access works and, when it does not, why."""

    succeeded: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SavedConfiguration:
    """The outcome of saving a provider configuration.

    ``credential_cache_unavailable`` is true when caching was asked for but
    the platform credential store could not take the secret; the
    configuration is saved without a credential reference.
    """

    configuration_id: ConfigurationId
    display_name: str
    credential_cached: bool
    credential_cache_unavailable: bool


@dataclass(frozen=True, slots=True)
class CapabilityEntry:
    """One known action and whether the provider supports it."""

    name: str
    supported: bool


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    """The actions of one configured provider, supported or not."""

    configuration_id: ConfigurationId
    display_name: str
    provider_kind: ProviderKind
    entries: tuple[CapabilityEntry, ...]


@dataclass(frozen=True, slots=True)
class OptionEntry:
    """One option a provider offers, such as a node or a storage."""

    kind: str
    value: str


@dataclass(frozen=True, slots=True)
class ServerSummary:
    """A server with its provider and whether this client manages it."""

    configuration_id: ConfigurationId
    provider_name: str
    server_identifier: str
    name: str
    state: str
    size: str
    addresses: tuple[str, ...]
    managed: bool


class FailureCategory(Enum):
    """Why a provider could not be read, for the hint the client shows."""

    UNREACHABLE = "unreachable"
    REJECTED = "rejected"
    CREDENTIALS = "credentials"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    """A provider that could not be read, reported next to the others."""

    configuration_id: ConfigurationId
    provider_name: str
    category: FailureCategory
    reason: str


@dataclass(frozen=True, slots=True)
class ServerListing:
    """The servers of every readable provider and the providers that failed."""

    servers: tuple[ServerSummary, ...]
    failures: tuple[ProviderFailure, ...]
