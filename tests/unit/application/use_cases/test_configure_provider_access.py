"""OC-001: saveProviderConfiguration, every precondition and exception."""

import pytest

from iac_node_forge.application.errors import (
    AccessNotValidatedError,
    DuplicateDisplayNameError,
)
from iac_node_forge.application.use_cases import ConfigureProviderAccess
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_kind import ProviderKind
from tests.unit.application.fakes import (
    FakeConfigurationStore,
    FakeCredentialStore,
    FakeProvider,
)
from tests.unit.application.samples import SECRET

KIND = ProviderKind.PROXMOX_VE
ADDRESS = "https://lab.example:8006"


def make(
    credential_store: FakeCredentialStore | None = None,
) -> tuple[ConfigureProviderAccess, FakeConfigurationStore, FakeCredentialStore]:
    configurations = FakeConfigurationStore()
    credentials = credential_store or FakeCredentialStore()
    use_case = ConfigureProviderAccess(
        FakeProvider(accepted_secret=SECRET),
        configurations,
        credentials,
        new_identifier=iter(["id-1", "id-2"]).__next__,
    )
    return use_case, configurations, credentials


def test_validate_access_reports_success_for_accepted_credentials() -> None:
    use_case, _, _ = make()

    check = use_case.validate_access(KIND, ADDRESS, Credentials(SECRET))

    assert check.succeeded


def test_validate_access_reports_the_reason_for_rejected_credentials() -> None:
    use_case, configurations, _ = make()

    check = use_case.validate_access(KIND, ADDRESS, Credentials("wrong"))

    assert not check.succeeded
    assert check.reason == "credentials rejected"
    assert configurations.load_all() == ()


def test_save_creates_configuration_without_reference_when_not_caching() -> None:
    use_case, configurations, credentials = make()
    use_case.validate_access(KIND, ADDRESS, Credentials(SECRET))

    saved = use_case.save_provider_configuration(KIND, ADDRESS, "Lab", False)

    (stored,) = configurations.load_all()
    assert stored.configuration_id == saved.configuration_id == "id-1"
    assert stored.provider_kind is KIND
    assert stored.display_name == "Lab"
    assert stored.address == ADDRESS
    assert stored.credential_reference is None
    assert not saved.credential_cached
    assert not saved.credential_cache_unavailable
    assert credentials.secrets == {}


def test_save_caches_credentials_under_a_new_reference_when_asked() -> None:
    use_case, configurations, credentials = make()
    use_case.validate_access(KIND, ADDRESS, Credentials(SECRET))

    saved = use_case.save_provider_configuration(KIND, ADDRESS, "Lab", True)

    (stored,) = configurations.load_all()
    reference = stored.credential_reference
    assert reference is not None
    assert credentials.get(reference).secret == SECRET
    assert saved.credential_cached
    assert SECRET not in repr(stored)


def test_save_is_rejected_without_a_successful_check() -> None:
    use_case, configurations, _ = make()

    with pytest.raises(AccessNotValidatedError):
        use_case.save_provider_configuration(KIND, ADDRESS, "Lab", False)

    assert configurations.load_all() == ()


def test_save_is_rejected_after_a_failed_check() -> None:
    use_case, configurations, _ = make()
    use_case.validate_access(KIND, ADDRESS, Credentials("wrong"))

    with pytest.raises(AccessNotValidatedError):
        use_case.save_provider_configuration(KIND, ADDRESS, "Lab", False)

    assert configurations.load_all() == ()


def test_save_is_rejected_for_an_address_that_was_not_validated() -> None:
    use_case, configurations, _ = make()
    use_case.validate_access(KIND, ADDRESS, Credentials(SECRET))

    with pytest.raises(AccessNotValidatedError):
        use_case.save_provider_configuration(KIND, "https://other:8006", "Lab", False)

    assert configurations.load_all() == ()


def test_save_is_rejected_when_the_display_name_exists() -> None:
    use_case, configurations, credentials = make()
    use_case.validate_access(KIND, ADDRESS, Credentials(SECRET))
    use_case.save_provider_configuration(KIND, ADDRESS, "Lab", False)
    use_case.validate_access(KIND, ADDRESS, Credentials(SECRET))

    with pytest.raises(DuplicateDisplayNameError):
        use_case.save_provider_configuration(KIND, ADDRESS, " lab ", True)

    assert len(configurations.load_all()) == 1
    assert credentials.secrets == {}


def test_save_without_validation_is_rejected_after_a_successful_save() -> None:
    use_case, _, _ = make()
    use_case.validate_access(KIND, ADDRESS, Credentials(SECRET))
    use_case.save_provider_configuration(KIND, ADDRESS, "Lab", False)

    with pytest.raises(AccessNotValidatedError):
        use_case.save_provider_configuration(KIND, ADDRESS, "Second", False)


def test_save_reports_unavailable_store_and_keeps_the_configuration() -> None:
    use_case, configurations, _ = make(FakeCredentialStore(unavailable=True))
    use_case.validate_access(KIND, ADDRESS, Credentials(SECRET))

    saved = use_case.save_provider_configuration(KIND, ADDRESS, "Lab", True)

    (stored,) = configurations.load_all()
    assert stored.credential_reference is None
    assert not saved.credential_cached
    assert saved.credential_cache_unavailable
