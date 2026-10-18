"""Plain results the use cases hand to presentation."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from iac_node_forge.domain.operation import Operation, OperationState
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.provider_value import ProviderValue


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
    provider_kind: ProviderKind
    category: FailureCategory
    reason: str


@dataclass(frozen=True, slots=True)
class ServerListing:
    """The servers of every readable provider and the providers that failed."""

    servers: tuple[ServerSummary, ...]
    failures: tuple[ProviderFailure, ...]


@dataclass(frozen=True, slots=True)
class Confirmation:
    """What the user confirmed for one power action.

    ``given`` is the yes to the stated action. ``typed_identifier`` is the
    server identifier the user retyped; it is required only for a server this
    client does not manage.
    """

    given: bool
    typed_identifier: str | None = None


@dataclass(frozen=True, slots=True)
class OperationSummary:
    """A recorded operation as presentation sees it; it holds no secret."""

    operation_id: str
    configuration_id: ConfigurationId
    server_identifier: str | None
    action: PowerAction
    state: OperationState
    started_at: datetime
    finished_at: datetime | None
    provider_reference: str | None
    detail: str | None

    @classmethod
    def of(cls, operation: Operation) -> "OperationSummary":
        """Return the summary of a domain operation."""
        return cls(
            operation.operation_id,
            operation.configuration_id,
            operation.server_identifier,
            operation.action,
            operation.state,
            operation.started_at,
            operation.finished_at,
            operation.provider_reference,
            operation.detail,
        )


class PowerOutcome(Enum):
    """How a power request ended, for the message the client shows.

    Only REQUESTED and REJECTED created an operation record; READY is the
    answer of a preview and means every precondition but the confirmation
    holds. In every other outcome no request was sent.
    """

    READY = "ready"
    REQUESTED = "requested"
    REJECTED = "rejected"
    UNSUPPORTED = "unsupported"
    NOT_FOUND = "not_found"
    ALREADY_IN_STATE = "already_in_state"
    NOT_CONFIRMED = "not_confirmed"
    IDENTIFIER_MISMATCH = "identifier_mismatch"


@dataclass(frozen=True, slots=True)
class PowerActionResult:
    """The answer to a power request or to its preview.

    ``reason`` says why no request was sent, or why the provider rejected it;
    ``operation`` is set for REQUESTED and REJECTED. ``server_name``,
    ``server_state`` and ``managed`` describe the server when it was found.
    """

    outcome: PowerOutcome
    configuration_id: ConfigurationId
    provider_name: str
    server_identifier: str
    action: PowerAction
    reason: str = ""
    operation: OperationSummary | None = None
    server_name: str | None = None
    server_state: str | None = None
    managed: bool = False


@dataclass(frozen=True, slots=True)
class DesiredConfigurationView:
    """A desired configuration as presentation sees it, and whether it is valid.

    ``problems`` names each choice that is not offered, out of bounds, missing
    or unknown; a view that is not valid was not saved. It holds the names of
    access keys, never a key value.
    """

    configuration_id: ConfigurationId
    name: str
    image: str
    access_key_names: tuple[str, ...]
    bootstrap_hint: str | None
    values: tuple[ProviderValue, ...]
    valid: bool
    problems: tuple[str, ...] = ()


class ProvisionOutcome(Enum):
    """How a preview, a request or a completion of a provisioning ended.

    READY is the answer of a preview and means every precondition but the
    confirmation holds. REQUESTED means a create request was sent and its
    operation is recorded. COMPLETED means the server is recorded as managed.
    INCOMPLETE means the operation is unfinished, unknown or names no server.
    REJECTED means the provider refused the request or the operation failed.
    In UNSUPPORTED, INVALID, NAME_EXISTS, INTENT_PENDING and NOT_CONFIRMED no
    request was sent.
    """

    READY = "ready"
    REQUESTED = "requested"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    REJECTED = "rejected"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"
    NAME_EXISTS = "name_exists"
    INTENT_PENDING = "intent_pending"
    NOT_CONFIRMED = "not_confirmed"


@dataclass(frozen=True, slots=True)
class ProvisionPreview:
    """What a provisioning would create and the commitment it makes.

    ``outcome`` is READY when the creation can be asked for, otherwise the
    reason it cannot; ``cost_consequence`` is empty when the provider imposes
    none. ``target`` and ``size`` are the values that place and size the
    server, as ``name value`` pairs.
    """

    outcome: ProvisionOutcome
    provider_kind: ProviderKind
    configuration_id: ConfigurationId
    provider_name: str
    desired_name: str
    server_name: str
    target: str
    image: str
    size: str
    values: tuple[ProviderValue, ...]
    cost_consequence: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    """The answer to a request for, or the completion of, a provisioning.

    ``reason`` says why nothing was sent, why the provider refused, or what is
    unfinished; ``operation`` is set once a request was recorded;
    ``state_left`` says what the provider holds after a failure.
    """

    outcome: ProvisionOutcome
    reason: str = ""
    operation: OperationSummary | None = None
    server_identifier: str | None = None
    state_left: str | None = None
