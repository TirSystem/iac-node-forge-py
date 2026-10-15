"""Plain-text formatting shared by the commands."""

from collections.abc import Sequence
from typing import TextIO

NO_VALUE = "-"
_COLUMN_GAP = "  "


def write_line(stream: TextIO, text: str = "") -> None:
    """Write one line, with a newline, to a stream."""
    stream.write(text + "\n")


def format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """Return the lines of a left-aligned table with a header line."""
    widths = [
        max(len(cell) for cell in column) for column in zip(headers, *rows, strict=True)
    ]
    return [
        _COLUMN_GAP.join(
            cell.ljust(width) for cell, width in zip(row, widths, strict=True)
        ).rstrip()
        for row in (headers, *rows)
    ]
