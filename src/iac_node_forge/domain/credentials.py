"""A secret carried for the duration of one call."""

from dataclasses import dataclass, field
from typing import NoReturn

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.errors import CredentialsNotPersistableError


@dataclass(frozen=True, slots=True, repr=False)
class Credentials:
    """Carries a secret for one call; it hides itself and cannot be persisted."""

    secret: str = field(repr=False)

    def __post_init__(self) -> None:
        require_text("Credentials", "secret", self.secret)

    def __repr__(self) -> str:
        return "Credentials(secret='***')"

    def __str__(self) -> str:
        return repr(self)

    def __reduce__(self) -> NoReturn:
        raise CredentialsNotPersistableError("Credentials must never be persisted")

    def __copy__(self) -> NoReturn:
        raise CredentialsNotPersistableError("Credentials must never be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise CredentialsNotPersistableError("Credentials must never be copied")
