"""Invariants of the domain classes."""

import copy
import pickle
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from iac_node_forge.domain.access_result import AccessResult
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.errors import (
    CredentialsNotPersistableError,
    DomainError,
    EmptyValueError,
    ManagedServerEndError,
    MissingCredentialError,
    MissingReasonError,
    NaiveTimestampError,
    NotSingleCredentialError,
)
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_option import ServerOption


def _configuration(
    configuration_id: str = "c1", display_name: str = "Prod", address: str = "https://a"
) -> ProviderConfiguration:
    return ProviderConfiguration(
        ConfigurationId(configuration_id), ProviderKind.CONTABO, display_name, address
    )


def test_provider_kind_has_proxmox_and_contabo() -> None:
    assert {k.name for k in ProviderKind} == {"PROXMOX_VE", "CONTABO"}


def test_errors_derive_from_domain_error() -> None:
    assert issubclass(EmptyValueError, DomainError)
    assert issubclass(MissingReasonError, DomainError)


def test_credential_reference_rejects_empty_name() -> None:
    with pytest.raises(EmptyValueError):
        CredentialReference("", "keyring")


def test_credential_reference_rejects_blank_store_kind() -> None:
    with pytest.raises(EmptyValueError):
        CredentialReference("lab", "  ")


def test_credential_reference_is_frozen() -> None:
    reference = CredentialReference("lab", "keyring")
    with pytest.raises(FrozenInstanceError):
        reference.name = "x"  # type: ignore[misc]


def test_credentials_repr_hides_secret() -> None:
    assert "hunter2" not in repr(Credentials.single("hunter2"))


def test_credentials_str_hides_secret() -> None:
    assert "hunter2" not in str(Credentials.single("hunter2"))


def test_credentials_rejects_empty_secret() -> None:
    with pytest.raises(EmptyValueError):
        Credentials.single("")


def test_credentials_rejects_empty_name() -> None:
    with pytest.raises(EmptyValueError):
        Credentials({" ": "x"})


def test_credentials_rejects_empty_value_among_several() -> None:
    with pytest.raises(EmptyValueError):
        Credentials({"a": "1", "b": " "})


def test_credentials_rejects_no_values() -> None:
    with pytest.raises(MissingCredentialError):
        Credentials({})


def test_credentials_single_is_named_token() -> None:
    credentials = Credentials.single("hunter2")

    assert credentials.names == ("token",)
    assert credentials.get("token") == "hunter2"
    assert credentials.single_value() == "hunter2"


def test_credentials_get_returns_each_value_by_name() -> None:
    credentials = Credentials({"a": "one", "b": "two"})

    assert credentials.get("a") == "one"
    assert credentials.get("b") == "two"
    assert credentials.names == ("a", "b")


def test_credentials_get_raises_for_absent_name() -> None:
    with pytest.raises(MissingCredentialError, match="'c'") as caught:
        Credentials({"a": "one"}).get("c")

    assert "one" not in str(caught.value)


def test_credentials_single_value_refuses_several() -> None:
    with pytest.raises(NotSingleCredentialError) as caught:
        Credentials({"a": "v-alpha", "b": "v-beta"}).single_value()

    assert "v-alpha" not in str(caught.value)


def test_credentials_repr_shows_names_but_no_values() -> None:
    credentials = Credentials({"client_id": "id-9", "api_password": "pw-9"})

    for text in (repr(credentials), str(credentials)):
        assert "client_id" in text
        assert "api_password" in text
        assert "id-9" not in text
        assert "pw-9" not in text


def test_credentials_values_are_read_only_and_detached() -> None:
    source = {"a": "one"}
    credentials = Credentials(source)
    source["a"] = "changed"

    assert credentials.get("a") == "one"
    with pytest.raises(TypeError):
        credentials.values["a"] = "x"  # type: ignore[index]


def test_credentials_with_several_values_cannot_be_pickled_or_copied() -> None:
    credentials = Credentials({"a": "one", "b": "two"})

    with pytest.raises(CredentialsNotPersistableError):
        pickle.dumps(credentials)
    with pytest.raises(CredentialsNotPersistableError):
        copy.copy(credentials)


def test_provider_kind_declares_credential_names() -> None:
    assert ProviderKind.PROXMOX_VE.credential_names == ("token",)
    assert ProviderKind.CONTABO.credential_names == (
        "client_id",
        "client_secret",
        "api_user",
        "api_password",
    )


def test_credentials_cannot_be_pickled() -> None:
    with pytest.raises(CredentialsNotPersistableError):
        pickle.dumps(Credentials.single("hunter2"))


def test_credentials_cannot_be_copied() -> None:
    with pytest.raises(CredentialsNotPersistableError):
        copy.copy(Credentials.single("hunter2"))


def test_credentials_cannot_be_deep_copied() -> None:
    with pytest.raises(CredentialsNotPersistableError):
        copy.deepcopy(Credentials.single("hunter2"))


def test_provider_configuration_defaults_to_no_credential_reference() -> None:
    assert _configuration().credential_reference is None


def test_provider_configuration_rejects_empty_configuration_id() -> None:
    with pytest.raises(EmptyValueError):
        _configuration(configuration_id="")


def test_provider_configuration_rejects_empty_display_name() -> None:
    with pytest.raises(EmptyValueError):
        _configuration(display_name="")


def test_provider_configuration_rejects_empty_address() -> None:
    with pytest.raises(EmptyValueError):
        _configuration(address="")


def test_access_result_success_needs_no_reason() -> None:
    assert AccessResult(succeeded=True).reason == ""


def test_access_result_failure_without_reason_is_rejected() -> None:
    with pytest.raises(MissingReasonError):
        AccessResult(succeeded=False)


def test_access_result_failure_with_blank_reason_is_rejected() -> None:
    with pytest.raises(MissingReasonError):
        AccessResult(succeeded=False, reason="  ")


def test_capability_rejects_empty_name() -> None:
    with pytest.raises(EmptyValueError):
        Capability("")


def test_capabilities_with_same_name_collapse_in_a_frozenset() -> None:
    assert len(frozenset({Capability("a"), Capability("a")})) == 1


def test_server_option_rejects_empty_kind() -> None:
    with pytest.raises(EmptyValueError):
        ServerOption("", "small")


def test_server_option_rejects_empty_value() -> None:
    with pytest.raises(EmptyValueError):
        ServerOption("size", "")


def test_server_rejects_empty_identifier() -> None:
    with pytest.raises(EmptyValueError):
        Server("", "web", "running", "small", (), False)


def test_server_rejects_empty_name() -> None:
    with pytest.raises(EmptyValueError):
        Server("1", "", "running", "small", (), False)


def test_server_rejects_empty_state() -> None:
    with pytest.raises(EmptyValueError):
        Server("1", "web", "", "small", (), False)


def test_server_accepts_empty_size_and_no_addresses() -> None:
    assert Server("1", "web", "running", "", (), True).managed


def test_managed_server_record_rejects_naive_timestamp() -> None:
    with pytest.raises(NaiveTimestampError):
        ManagedServerRecord(ConfigurationId("c1"), "1", datetime(2026, 1, 1))


def test_managed_server_record_rejects_empty_server_identifier() -> None:
    with pytest.raises(EmptyValueError):
        ManagedServerRecord(ConfigurationId("c1"), "", datetime.now(UTC))


def test_managed_server_record_rejects_empty_configuration_id() -> None:
    with pytest.raises(EmptyValueError):
        ManagedServerRecord(ConfigurationId(""), "1", datetime.now(UTC))


def test_managed_server_record_is_not_ended_by_default() -> None:
    record = ManagedServerRecord(ConfigurationId("c1"), "1", datetime.now(UTC))

    assert record.ended_at is None
    assert record.ended_by_operation_id is None
    assert not record.is_ended


def test_managed_server_record_is_ended_with_the_time_and_the_operation() -> None:
    recorded = datetime(2026, 10, 1, tzinfo=UTC)
    ended_at = datetime(2026, 10, 18, tzinfo=UTC)

    ended = ManagedServerRecord(ConfigurationId("c1"), "1", recorded).ended(
        ended_at, OperationId("op-1")
    )

    assert ended.is_ended
    assert ended.ended_at == ended_at
    assert ended.ended_by_operation_id == "op-1"
    assert ended.recorded_at == recorded


def test_managed_server_record_rejects_an_end_time_without_an_operation() -> None:
    with pytest.raises(ManagedServerEndError):
        ManagedServerRecord(
            ConfigurationId("c1"), "1", datetime.now(UTC), ended_at=datetime.now(UTC)
        )


def test_managed_server_record_rejects_an_operation_without_an_end_time() -> None:
    with pytest.raises(ManagedServerEndError):
        ManagedServerRecord(
            ConfigurationId("c1"),
            "1",
            datetime.now(UTC),
            ended_by_operation_id=OperationId("op-1"),
        )


def test_managed_server_record_rejects_a_naive_end_time() -> None:
    with pytest.raises(NaiveTimestampError):
        ManagedServerRecord(
            ConfigurationId("c1"),
            "1",
            datetime.now(UTC),
            ended_at=datetime(2026, 1, 1),
            ended_by_operation_id=OperationId("op-1"),
        )
