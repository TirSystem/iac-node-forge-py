"""The shape of one line of the JSON Lines file, shared by the record modules."""

from iac_node_forge.application.errors import ConfigurationFormatError

type Row = dict[str, str | int | None]
"""One parsed line: field names to text, an integer, or None for a nullable one."""


def text(row: Row, key: str) -> str:
    """Return a field that must be text; raises ConfigurationFormatError on null."""
    value = row[key]
    if not isinstance(value, str):
        raise ConfigurationFormatError(f"field {key!r} must be text")
    return value


def optional_text(row: Row, key: str) -> str | None:
    """Return a text field, or None when it is null."""
    value = row[key]
    if value is not None and not isinstance(value, str):
        raise ConfigurationFormatError(f"field {key!r} must be text")
    return value


def integer(row: Row, key: str) -> int:
    """Return a field that must be a whole number."""
    value = row[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigurationFormatError(f"field {key!r} must be a whole number")
    return value
