"""The shape of one line of the JSON Lines file, shared by the record modules."""

from iac_node_forge.application.errors import ConfigurationFormatError

type Row = dict[str, str | None]
"""One parsed line: field names to text, or None for a nullable field."""


def text(row: Row, key: str) -> str:
    """Return a field that must be text; raises ConfigurationFormatError on null."""
    value = row[key]
    if value is None:
        raise ConfigurationFormatError(f"field {key!r} must not be null")
    return value
