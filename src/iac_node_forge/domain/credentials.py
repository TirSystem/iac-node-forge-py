"""The named secret values carried for the duration of one call."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import NoReturn

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.errors import (
    CredentialsNotPersistableError,
    MissingCredentialError,
    NotSingleCredentialError,
)

SINGLE_VALUE_NAME = "token"


@dataclass(frozen=True, slots=True, repr=False)
class Credentials:
    """Carries named secret values for one call.

    It hides the values from ``repr`` and ``str`` (names may be shown) and
    cannot be persisted, pickled or copied.
    """

    values: Mapping[str, str] = field(repr=False)

    def __post_init__(self) -> None:
        if not self.values:
            raise MissingCredentialError("Credentials must hold at least one value")
        for name, value in self.values.items():
            require_text("Credentials", "name", name)
            require_text("Credentials", f"value of {name!r}", value)
        # A private read-only copy, so the caller's mapping cannot change it.
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    @classmethod
    def single(cls, secret: str) -> "Credentials":
        """Return credentials holding one value, named ``token``."""
        return cls({SINGLE_VALUE_NAME: secret})

    @property
    def names(self) -> tuple[str, ...]:
        """Return the names of the held values, in insertion order."""
        return tuple(self.values)

    def get(self, name: str) -> str:
        """Return the value called ``name``; raise when there is none."""
        try:
            return self.values[name]
        except KeyError:
            raise MissingCredentialError(
                f"Credentials hold no value named {name!r}"
            ) from None

    def single_value(self) -> str:
        """Return the only value; refuse when there are several."""
        if len(self.values) != 1:
            raise NotSingleCredentialError(
                f"Credentials hold {len(self.values)} values, not one"
            )
        return next(iter(self.values.values()))

    def __repr__(self) -> str:
        names = ", ".join(f"{name}='***'" for name in self.values)
        return f"Credentials({names})"

    def __str__(self) -> str:
        return repr(self)

    def __reduce__(self) -> NoReturn:
        raise CredentialsNotPersistableError("Credentials must never be persisted")

    def __copy__(self) -> NoReturn:
        raise CredentialsNotPersistableError("Credentials must never be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise CredentialsNotPersistableError("Credentials must never be copied")
