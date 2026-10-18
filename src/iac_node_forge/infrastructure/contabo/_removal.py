"""What a Contabo cancellation is made of: its consequence, block and dates.

Pure functions, no network. The provider sends the one request
``POST /v1/compute/instances/{id}/cancel`` (ADR-0008, C14). Contabo does not
delete: the instance gets the status ``cancelled`` and a ``cancelDate``, and the
contract runs on until then. The rule that sets the date is not stated, so the
product reports the date the provider gives and never computes one.
"""

from collections.abc import Mapping

from iac_node_forge.application.ports.server_removal_port import RemovalBlock
from iac_node_forge.domain.server import Server

STATUS_CANCELLED = "cancelled"
CANCEL_DATE_KEY = "cancelDate"


def cancel_path(instances_path: str, instance_id: int) -> str:
    """Return the path of the cancellation of one instance."""
    return f"{instances_path}/{instance_id}/cancel"


def consequence(server: Server) -> str:
    """State what a removal does: a cancellation, with a date known afterwards."""
    return (
        f"This requests the cancellation of instance {server.server_identifier} "
        "at Contabo. The contract and its billing continue until the effective "
        "date that Contabo reports after the request, and this client cannot "
        "withdraw a cancellation."
    )


def block(server: Server) -> RemovalBlock | None:
    """Return ALREADY_REMOVED for an instance that is already cancelled."""
    return RemovalBlock.ALREADY_REMOVED if server.state == STATUS_CANCELLED else None


def cancel_date(item: Mapping[str, object]) -> str | None:
    """Return the cancel date as the provider reports it, or None when absent."""
    value = item.get(CANCEL_DATE_KEY)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def requested_detail(date: str | None) -> str:
    """Say that a cancellation was requested and which date the answer gave."""
    if date is None:
        return "cancellation requested, no date reported"
    return f"cancellation requested, effective {date}"


def cancelled_detail(date: str | None) -> str:
    """Say that the instance is cancelled and which date the provider reports."""
    if date is None:
        return "cancelled, no date was reported"
    return f"cancelled, effective {date}"
