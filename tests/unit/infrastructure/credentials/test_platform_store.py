import json

import pytest

from iac_node_forge.application.errors import (
    CredentialEntryInvalidError,
    CredentialNotFoundError,
    CredentialStoreUnavailableError,
)
from iac_node_forge.application.ports import CredentialStore
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.infrastructure.credentials import PlatformCredentialStore
from tests.unit.infrastructure.credentials.memory_keyring import MemoryKeyring

REFERENCE = CredentialReference("lab-proxmox", "keyring")
SERVICE = "iac-node-forge"
NAME = "lab-proxmox"


def test_store_satisfies_the_port() -> None:
    assert isinstance(PlatformCredentialStore(), CredentialStore)


def test_put_then_get_round_trips_a_single_value(
    memory_keyring: MemoryKeyring,
) -> None:
    store = PlatformCredentialStore()

    store.put(REFERENCE, Credentials.single("token-123"))

    restored = store.get(REFERENCE)
    assert restored.names == ("token",)
    assert restored.single_value() == "token-123"


def test_put_then_get_round_trips_several_values_as_one_entry(
    memory_keyring: MemoryKeyring,
) -> None:
    store = PlatformCredentialStore()
    values = {
        "client_id": "i",
        "client_secret": "s",
        "api_user": "u",
        "api_password": "p",
    }

    store.put(REFERENCE, Credentials(values))

    restored = store.get(REFERENCE)
    assert dict(restored.values) == values
    assert restored.names == tuple(values)
    assert json.loads(memory_keyring.get_password(SERVICE, NAME) or "") == values


def test_put_replaces_the_whole_entry(memory_keyring: MemoryKeyring) -> None:
    store = PlatformCredentialStore()
    store.put(REFERENCE, Credentials({"a": "1", "b": "2"}))

    store.put(REFERENCE, Credentials({"c": "3"}))

    assert dict(store.get(REFERENCE).values) == {"c": "3"}


def test_get_reads_a_plain_string_of_the_first_version_as_token(
    memory_keyring: MemoryKeyring,
) -> None:
    memory_keyring.set_password(SERVICE, NAME, "root@pam!iacnf=abc")

    restored = PlatformCredentialStore().get(REFERENCE)

    assert restored.names == ("token",)
    assert restored.get("token") == "root@pam!iacnf=abc"


def test_get_raises_not_found_for_unknown_reference(
    memory_keyring: MemoryKeyring,
) -> None:
    with pytest.raises(CredentialNotFoundError, match="lab-proxmox"):
        PlatformCredentialStore().get(REFERENCE)


@pytest.mark.parametrize(
    "entry",
    [
        '{"token": "s3cr3t-in-broken-json"',
        '{"token": 12345, "s3cr3t": "x"}',
        '{"s3cr3t-name": ""}',
        "{}",
    ],
)
def test_get_raises_invalid_for_a_corrupt_entry_without_its_content(
    memory_keyring: MemoryKeyring, entry: str
) -> None:
    memory_keyring.set_password(SERVICE, NAME, entry)

    with pytest.raises(CredentialEntryInvalidError, match="lab-proxmox") as caught:
        PlatformCredentialStore().get(REFERENCE)

    assert "s3cr3t" not in str(caught.value)
    assert "s3cr3t" not in repr(caught.value)
    assert "12345" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_put_raises_unavailable_without_backend(failing_keyring: None) -> None:
    credentials = Credentials({"a": "top-secret", "b": "other-secret"})

    with pytest.raises(CredentialStoreUnavailableError) as caught:
        PlatformCredentialStore().put(REFERENCE, credentials)

    for secret in ("top-secret", "other-secret"):
        assert secret not in str(caught.value)
        assert secret not in repr(caught.value)
    assert caught.value.__cause__ is None


def test_get_raises_unavailable_without_backend(failing_keyring: None) -> None:
    with pytest.raises(CredentialStoreUnavailableError):
        PlatformCredentialStore().get(REFERENCE)
