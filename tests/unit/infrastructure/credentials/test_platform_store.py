import pytest

from iac_node_forge.application.errors import (
    CredentialNotFoundError,
    CredentialStoreUnavailableError,
)
from iac_node_forge.application.ports import CredentialStore
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.infrastructure.credentials import PlatformCredentialStore
from tests.unit.infrastructure.credentials.memory_keyring import MemoryKeyring

REFERENCE = CredentialReference("lab-proxmox", "keyring")


def test_store_satisfies_the_port() -> None:
    assert isinstance(PlatformCredentialStore(), CredentialStore)


def test_put_then_get_round_trips(memory_keyring: MemoryKeyring) -> None:
    store = PlatformCredentialStore()

    store.put(REFERENCE, Credentials("token-123"))

    assert store.get(REFERENCE).secret == "token-123"
    assert memory_keyring.get_password("iac-node-forge", "lab-proxmox") == "token-123"


def test_get_raises_not_found_for_unknown_reference(
    memory_keyring: MemoryKeyring,
) -> None:
    with pytest.raises(CredentialNotFoundError, match="lab-proxmox"):
        PlatformCredentialStore().get(REFERENCE)


def test_put_raises_unavailable_without_backend(failing_keyring: None) -> None:
    with pytest.raises(CredentialStoreUnavailableError) as caught:
        PlatformCredentialStore().put(REFERENCE, Credentials("top-secret"))

    assert "top-secret" not in str(caught.value)
    assert "top-secret" not in repr(caught.value)
    assert caught.value.__cause__ is None


def test_get_raises_unavailable_without_backend(failing_keyring: None) -> None:
    with pytest.raises(CredentialStoreUnavailableError):
        PlatformCredentialStore().get(REFERENCE)
