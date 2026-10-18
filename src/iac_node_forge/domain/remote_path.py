"""Paths on a server (POSIX) and paths inside a manifest (ADR-0009)."""

from iac_node_forge.domain.errors import InvalidRemotePathError

_FORBIDDEN = ("\x00", "\n", "\r")
ROOT = "/"


def validate_remote_path(owner: str, path: str) -> str:
    """Return ``path`` when it is absolute, normalised POSIX text.

    Raises InvalidRemotePathError for a relative path, a NUL or line break, an
    empty, ``.`` or ``..`` component, or a trailing slash. The root ``/`` is
    valid. The value is not repeated in the message.
    """
    if not path.startswith(ROOT):
        raise InvalidRemotePathError(f"{owner} must be an absolute path")
    _require_clean(owner, path)
    if path != ROOT:
        _require_components(owner, path[1:])
    return path


def validate_relative_path(owner: str, path: str) -> str:
    """Return ``path`` when it is relative, normalised POSIX text, not empty."""
    if not path or path.startswith(ROOT):
        raise InvalidRemotePathError(f"{owner} must be a relative path")
    _require_clean(owner, path)
    _require_components(owner, path)
    return path


def join_remote(root: str, relative: str) -> str:
    """Return the absolute path of ``relative`` under the absolute ``root``."""
    validate_remote_path("root", root)
    validate_relative_path("relative", relative)
    return root.rstrip(ROOT) + ROOT + relative


def _require_clean(owner: str, path: str) -> None:
    if any(character in path for character in _FORBIDDEN):
        raise InvalidRemotePathError(f"{owner} holds a NUL or a line break")


def _require_components(owner: str, path: str) -> None:
    for component in path.split(ROOT):
        if component in {"", ".", ".."}:
            raise InvalidRemotePathError(
                f"{owner} is not normalised: empty, '.' or '..' component"
            )
