"""Each fake satisfies its port, checked by mypy and at runtime."""

from datetime import UTC, datetime

from iac_node_forge.application.ports import (
    ConfigurationStore,
    CredentialPrompt,
    CredentialStore,
    ProviderPort,
)
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_option import ServerOption
from tests.unit.application.fakes import (
    FakeConfigurationStore,
    FakeCredentialPrompt,
    FakeCredentialStore,
    FakeProvider,
)

CONFIG_ID = ConfigurationId("cfg-1")
CONFIGURATION = ProviderConfiguration(
    CONFIG_ID, ProviderKind.PROXMOX_VE, "Lab", "https://pve.example:8006"
)
SERVER = Server("100", "web", "running", "2cpu", ("10.0.0.5",), False)


def test_fake_provider_satisfies_provider_port() -> None:
    port: ProviderPort = FakeProvider()
    assert isinstance(port, ProviderPort)


def test_fake_configuration_store_satisfies_configuration_store() -> None:
    port: ConfigurationStore = FakeConfigurationStore()
    assert isinstance(port, ConfigurationStore)


def test_fake_credential_store_satisfies_credential_store() -> None:
    port: CredentialStore = FakeCredentialStore()
    assert isinstance(port, CredentialStore)


def test_fake_credential_prompt_satisfies_credential_prompt() -> None:
    port: CredentialPrompt = FakeCredentialPrompt()
    assert isinstance(port, CredentialPrompt)


def test_provider_validate_access_succeeds_for_accepted_secret() -> None:
    result = FakeProvider().validate_access("a", Credentials.single("secret"))
    assert result.succeeded


def test_provider_validate_access_fails_with_reason_for_wrong_secret() -> None:
    result = FakeProvider().validate_access("a", Credentials.single("wrong"))
    assert not result.succeeded
    assert result.reason != ""


def test_provider_discovers_capabilities_as_frozenset() -> None:
    caps = frozenset({Capability("list_servers")})
    assert FakeProvider(capabilities=caps).discover_capabilities(CONFIGURATION) == caps


def test_provider_list_server_options_filters_by_kind() -> None:
    size = ServerOption("size", "small")
    image = ServerOption("image", "debian")
    provider = FakeProvider(options=(size, image))
    assert provider.list_server_options(CONFIGURATION, "size") == (size,)


def test_provider_get_server_returns_matching_server() -> None:
    provider = FakeProvider(servers=(SERVER,))
    assert provider.get_server(CONFIGURATION, "100") == SERVER


def test_configuration_store_load_all_returns_saved_configuration() -> None:
    store = FakeConfigurationStore()
    store.save(CONFIGURATION)
    assert store.load_all() == (CONFIGURATION,)


def test_configuration_store_managed_servers_filters_by_configuration() -> None:
    now = datetime.now(UTC)
    mine = ManagedServerRecord(CONFIG_ID, "100", now)
    other = ManagedServerRecord(ConfigurationId("cfg-2"), "200", now)
    store = FakeConfigurationStore(managed=(mine, other))
    assert store.managed_servers(CONFIG_ID) == (mine,)


def test_credential_store_get_returns_stored_credentials() -> None:
    store = FakeCredentialStore()
    reference = CredentialReference("lab", "keyring")
    credentials = Credentials.single("s3cret")
    store.put(reference, credentials)
    assert store.get(reference) == credentials


def test_credential_prompt_returns_credentials_and_records_provider() -> None:
    prompt = FakeCredentialPrompt("typed")
    credentials = prompt.ask(ProviderKind.CONTABO)
    assert credentials.names == ProviderKind.CONTABO.credential_names
    assert credentials.get("client_id") == "typed"
    assert prompt.asked == [ProviderKind.CONTABO]
