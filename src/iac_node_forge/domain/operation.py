"""One requested action and its recorded outcome."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import NewType

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.errors import (
    NaiveTimestampError,
    OperationStateError,
    OperationTextTooLongError,
)
from iac_node_forge.domain.power_action import PowerAction
from iac_node_forge.domain.provider_configuration import ConfigurationId

OperationId = NewType("OperationId", str)

MAX_DETAIL_LENGTH = 500
"""Longest ``detail`` an Operation accepts, in characters."""

MAX_PROVIDER_REFERENCE_LENGTH = 200
"""Longest ``provider_reference`` an Operation accepts, in characters."""

_ELLIPSIS = "..."


class OperationState(StrEnum):
    """Where an operation stands; only RUNNING is not final."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


def bound_detail(text: str) -> str | None:
    """Return ``text`` as one line of at most MAX_DETAIL_LENGTH characters.

    Whitespace runs (including line breaks) become one space; a longer text is
    cut and ends with ``...``. Returns None when nothing is left. An adapter
    uses it for every text that comes from a provider, because an Operation
    rejects a detail that is too long rather than cutting it.
    """
    line = " ".join(text.split())
    if not line:
        return None
    if len(line) <= MAX_DETAIL_LENGTH:
        return line
    return line[: MAX_DETAIL_LENGTH - len(_ELLIPSIS)] + _ELLIPSIS


@dataclass(frozen=True, slots=True)
class Operation:
    """One requested action (a power action or a provision) and its outcome.

    It holds identifiers, an action, a state, two timestamps and two bounded
    texts (a provider task identifier and a detail). It has no field for a
    credential, a token or a header, and a caller never passes one: the
    adapters build ``detail`` from the provider's status or exit status only.

    Invariants: a RUNNING operation has no ``finished_at``; every other state
    has one, not before ``started_at``; both timestamps carry a timezone;
    ``detail`` and ``provider_reference``, when given, are not blank and within
    their bounds (``bound_detail`` shapes a provider text to fit);
    ``server_identifier`` is None for a provision until the provider names the
    server, and is not blank otherwise.
    """

    operation_id: OperationId
    configuration_id: ConfigurationId
    server_identifier: str | None
    action: PowerAction
    state: OperationState
    started_at: datetime
    finished_at: datetime | None = None
    provider_reference: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        require_text("Operation", "operation_id", self.operation_id)
        require_text("Operation", "configuration_id", self.configuration_id)
        if self.server_identifier is not None:
            require_text("Operation", "server_identifier", self.server_identifier)
        self._check_times()
        self._check_text(
            "provider_reference", self.provider_reference, MAX_PROVIDER_REFERENCE_LENGTH
        )
        self._check_text("detail", self.detail, MAX_DETAIL_LENGTH)

    @property
    def has_ended(self) -> bool:
        """Whether the operation is in a final state."""
        return self.state is not OperationState.RUNNING

    def ended(
        self, state: OperationState, finished_at: datetime, detail: str | None
    ) -> "Operation":
        """Return this running operation, ended in ``state`` at ``finished_at``.

        Raises OperationStateError when ``state`` is RUNNING or this operation
        has already ended.
        """
        if self.has_ended:
            raise OperationStateError("an ended operation is not changed")
        if state is OperationState.RUNNING:
            raise OperationStateError("an operation cannot end in state running")
        return replace(self, state=state, finished_at=finished_at, detail=detail)

    def _check_times(self) -> None:
        if self.started_at.tzinfo is None:
            raise NaiveTimestampError("Operation.started_at needs a timezone")
        if self.finished_at is None:
            if self.has_ended:
                raise OperationStateError(
                    f"an operation in state {self.state.value} needs finished_at"
                )
            return
        if self.finished_at.tzinfo is None:
            raise NaiveTimestampError("Operation.finished_at needs a timezone")
        if not self.has_ended:
            raise OperationStateError("a running operation has no finished_at")
        if self.finished_at < self.started_at:
            raise OperationStateError("Operation.finished_at is before started_at")

    @staticmethod
    def _check_text(field_name: str, value: str | None, limit: int) -> None:
        if value is None:
            return
        require_text("Operation", field_name, value)
        if len(value) > limit:
            raise OperationTextTooLongError(
                f"Operation.{field_name} has {len(value)} characters; "
                f"the limit is {limit}"
            )
