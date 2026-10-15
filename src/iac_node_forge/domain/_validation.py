"""Shared validation helpers for the domain classes."""

from iac_node_forge.domain.errors import EmptyValueError


def require_text(owner: str, field_name: str, value: str) -> None:
    """Raise EmptyValueError when ``value`` is empty or only whitespace."""
    if not value.strip():
        raise EmptyValueError(f"{owner}.{field_name} must not be empty")
