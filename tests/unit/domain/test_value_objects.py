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
    MissingReasonError,
    NaiveTimestampError,
)
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
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
    assert "hunter2" not in repr(Credentials("hunter2"))


def test_credentials_str_hides_secret() -> None:
    assert "hunter2" not in str(Credentials("hunter2"))


def test_credentials_rejects_empty_secret() -> None:
    with pytest.raises(EmptyValueError):
        Credentials("")


def test_credentials_cannot_be_pickled() -> None:
    with pytest.raises(CredentialsNotPersistableError):
        pickle.dumps(Credentials("hunter2"))


def test_credentials_cannot_be_copied() -> None:
    with pytest.raises(CredentialsNotPersistableError):
        copy.copy(Credentials("hunter2"))


def test_credentials_cannot_be_deep_copied() -> None:
    with pytest.raises(CredentialsNotPersistableError):
        copy.deepcopy(Credentials("hunter2"))


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
