"""An in-memory keyring backend, so tests never touch the real OS store."""

from collections.abc import Iterator

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends import fail


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self._items: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self._items[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self._items.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self._items.pop((service, username), None)


@pytest.fixture
def memory_keyring() -> Iterator[MemoryKeyring]:
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def failing_keyring() -> Iterator[None]:
    previous = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())  # type: ignore[no-untyped-call]
    yield
    keyring.set_keyring(previous)
