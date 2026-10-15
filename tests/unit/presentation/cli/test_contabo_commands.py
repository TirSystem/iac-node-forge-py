"""Command-line behaviour for Contabo and for mixed providers, with fakes.

The docstrings name the acceptance criterion of the user story each test shows.
"""

import io
from dataclasses import replace

import pytest

from iac_node_forge.application.errors import (
    ProviderAuthenticationError,
    ProviderServiceError,
    ProviderUnreachableError,
)
from iac_node_forge.application.ports import ProviderPort
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_configuration import (
    ConfigurationId,
    ProviderConfiguration,
)
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.domain.server_option import ServerOption
from iac_node_forge.infrastructure.contabo import ContaboProvider
from iac_node_forge.infrastructure.contabo import provider as contabo_module
from iac_node_forge.infrastructure.proxmox import ProxmoxProvider
from iac_node_forge.presentation.cli.composition import build_services
from iac_node_forge.presentation.cli.settings import VERIFY_ENV_VAR
from tests.unit.application.fakes import FailingProvider, FakeProvider
from tests.unit.application.samples import LAB, SECRET, WEB
from tests.unit.presentation.cli.harness import Harness

CLOUD_ID = ConfigurationId("cfg-cloud")
CLOUD = ProviderConfiguration(
    CLOUD_ID, ProviderKind.CONTABO, "Cloud", "https://api.contabo.com"
)
CONTABO_WORDS = "client ID, client secret, API user and API password"
CONTABO_NAMES = ProviderKind.CONTABO.credential_names


def with_cloud(harness: Harness) -> Harness:
    harness.configurations.save(CLOUD)
    return harness


def test_provider_add_for_contabo_uses_the_default_address() -> None:
    """US-001.01: Contabo needs no address; the public API address is used."""
    harness = Harness(answers=["Cloud"])

    result = harness.run("provider", "add", "--kind", "contabo")

    assert result.code == 0, result.err
    assert "Access to https://api.contabo.com works." in result.out
    (saved,) = harness.configurations.load_all()
    assert saved.provider_kind is ProviderKind.CONTABO
    assert saved.address == "https://api.contabo.com"
    assert saved.display_name == "Cloud"
    assert harness.prompt.asked == [ProviderKind.CONTABO]


def test_provider_add_for_contabo_accepts_another_address() -> None:
    harness = Harness()

    result = harness.run(
        "provider",
        "add",
        "--kind",
        "contabo",
        "--address",
        "https://proxy.example",
        "--name",
        "Cloud",
    )

    assert result.code == 0, result.err
    (saved,) = harness.configurations.load_all()
    assert saved.address == "https://proxy.example"


def test_provider_add_for_proxmox_still_asks_for_the_address() -> None:
    harness = Harness(answers=["https://lab.example:8006", "Lab"])

    result = harness.run("provider", "add")

    assert result.code == 0
    (saved,) = harness.configurations.load_all()
    assert saved.address == "https://lab.example:8006"


def test_provider_add_caches_the_four_contabo_values_as_one_entry() -> None:
    """US-001.01 and ADR-0005: four values, one entry, none in configuration."""
    harness = Harness()

    result = harness.run(
        "provider", "add", "--kind", "contabo", "--name", "Cloud", "--cache-credential"
    )

    assert result.code == 0, result.err
    (saved,) = harness.configurations.load_all()
    assert saved.credential_reference is not None
    stored = harness.credential_store.get(saved.credential_reference)
    assert stored.names == CONTABO_NAMES
    assert "Contabo credentials are kept" in result.out
    assert SECRET not in result.out + result.err + repr(saved)


def test_provider_add_rejection_for_contabo_names_the_contabo_credentials() -> None:
    """US-001.01: the reason is stated in Contabo's words, nothing is saved."""
    harness = Harness()
    harness.prompt.secret = "not-the-password"

    result = harness.run("provider", "add", "--kind", "contabo", "--name", "Cloud")

    assert result.code == 1
    assert CONTABO_WORDS in result.err
    assert "token" not in result.err.lower()
    assert "Nothing was saved" in result.err
    assert harness.configurations.load_all() == ()
    assert "not-the-password" not in result.out + result.err


def test_provider_add_rejects_an_unknown_kind_as_a_usage_error() -> None:
    result = Harness().run("provider", "add", "--kind", "hetzner")

    assert result.code == 2
    assert "invalid choice" in result.err


def test_provider_add_help_names_both_providers_credentials() -> None:
    result = Harness().run("provider", "add", "--help")

    assert result.code == 0
    out = " ".join(result.out.split())
    for text in ("proxmox_ve", "contabo", "API token", CONTABO_WORDS):
        assert text in out
    assert "https://api.contabo.com" in out


def test_options_help_names_the_kinds_of_each_provider() -> None:
    result = Harness().run("options", "--help")

    out = " ".join(result.out.split())
    assert "Proxmox VE: node, storage" in out
    assert "Contabo: region, image" in out


def test_rejected_contabo_credentials_use_contabo_words() -> None:
    harness = with_cloud(
        Harness(provider=FailingProvider(ProviderAuthenticationError("401")))
    )

    result = harness.run("server", "Cloud", "100")

    assert result.code == 1
    assert f"provider 'Cloud' (Contabo) rejected the {CONTABO_WORDS}" in result.err
    assert "token" not in result.err.lower()
    assert "provider add" in result.err


def test_unreachable_contabo_names_the_kind_and_hints_at_verify() -> None:
    harness = with_cloud(
        Harness(provider=FailingProvider(ProviderUnreachableError("timed out")))
    )

    result = harness.run("capabilities", "Cloud")

    assert result.code == 1
    assert "provider 'Cloud' (Contabo) could not be reached: timed out" in result.err
    assert VERIFY_ENV_VAR in result.err


def test_capabilities_name_the_provider_kind() -> None:
    """US-001.02: capabilities work the same way for Contabo."""
    harness = with_cloud(Harness())

    result = harness.run("capabilities", "Cloud")

    assert result.code == 0
    assert "(contabo)" in result.out.splitlines()[0]


def test_options_list_regions_for_contabo() -> None:
    """US-001.02: the data centers of Contabo are listed."""
    harness = with_cloud(
        Harness(provider=FakeProvider(options=(ServerOption("region", "EU1"),)))
    )

    result = harness.run("options", "Cloud", "region")

    assert result.code == 0
    assert result.out.splitlines()[1:] == ["  EU1"]


@pytest.mark.parametrize(
    ("configuration", "kind", "valid"),
    [(CLOUD, "node", "region, image"), (LAB, "region", "node, storage")],
)
def test_unknown_option_kind_lists_the_valid_kinds_of_that_provider(
    configuration: ProviderConfiguration, kind: str, valid: str
) -> None:
    """US-001.02: the real adapters name the kinds their provider offers."""
    provider: ProviderPort = (
        ContaboProvider(Credentials(dict.fromkeys(CONTABO_NAMES, "x")))
        if configuration.provider_kind is ProviderKind.CONTABO
        else ProxmoxProvider(Credentials.single("x"))
    )
    harness = Harness(provider=provider)
    harness.configurations.save(configuration)

    result = harness.run("options", configuration.display_name, kind)

    assert result.code == 1
    assert f"valid kinds: {valid}" in result.err
    assert "Traceback" not in result.err


def test_option_kinds_of_the_adapters_match_the_provider_kinds() -> None:
    assert ProviderKind.CONTABO.option_kinds == (
        contabo_module.OPTION_KIND_REGION,
        contabo_module.OPTION_KIND_IMAGE,
    )
    assert ProviderKind.PROXMOX_VE.option_kinds == ("node", "storage")


def test_servers_of_mixed_providers_are_listed_together() -> None:
    """US-001.06: servers of Proxmox VE and Contabo in one list."""
    harness = Harness(
        providers_by_secret={
            "lab-secret": FakeProvider(servers=(WEB,)),
            "cloud-secret": FakeProvider(servers=(replace(WEB, name="vps"),)),
        }
    )
    _save_with_secret(harness, LAB, "lab-secret")
    _save_with_secret(harness, CLOUD, "cloud-secret")

    result = harness.run("servers")

    assert result.code == 0
    providers = [line.split()[0] for line in result.out.splitlines()[1:]]
    assert providers == ["Lab", "Cloud"]


def test_servers_report_a_failing_contabo_without_hiding_proxmox() -> None:
    """US-001.06: a failing provider does not hide the servers of the other."""
    harness = Harness(
        providers_by_secret={
            "lab-secret": FakeProvider(servers=(WEB,)),
            "cloud-secret": FailingProvider(ProviderServiceError("status 500")),
        }
    )
    _save_with_secret(harness, LAB, "lab-secret")
    _save_with_secret(harness, CLOUD, "cloud-secret")

    result = harness.run("servers")

    assert result.code == 1
    assert "web" in result.out
    assert "provider 'Cloud' (Contabo) could not be read (other)" in result.err


def test_corrupt_credential_entry_falls_back_to_the_prompt() -> None:
    reference = CredentialReference("contabo-cfg-cloud", "keyring")
    harness = Harness(provider=FakeProvider(servers=(WEB,)))
    harness.configurations.save(replace(CLOUD, credential_reference=reference))
    harness.credential_store.invalid.add(reference)

    result = harness.run("servers")

    assert result.code == 0
    assert harness.prompt.asked == [ProviderKind.CONTABO]
    warnings = [line for line in result.err.splitlines() if line]
    assert len(warnings) == 1
    assert "'contabo-cfg-cloud'" in warnings[0]
    assert "ignored" in warnings[0]
    assert SECRET not in result.err


def test_stored_entry_with_other_names_is_ignored_like_a_corrupt_one() -> None:
    reference = CredentialReference("contabo-cfg-cloud", "keyring")
    harness = Harness(provider=FakeProvider(servers=(WEB,)))
    harness.configurations.save(replace(CLOUD, credential_reference=reference))
    harness.credential_store.put(reference, Credentials.single("only-a-token"))

    result = harness.run("servers")

    assert result.code == 0
    assert "'contabo-cfg-cloud'" in result.err
    assert "only-a-token" not in result.err
    assert harness.prompt.asked == [ProviderKind.CONTABO]


def test_composition_builds_contabo_with_the_injected_authentication_url() -> None:
    stream = io.StringIO()
    services = build_services(
        {}, stream, stream, contabo_auth_url="http://127.0.0.1:1/token"
    )

    provider = services.provider_factory(
        ProviderKind.CONTABO, Credentials(dict.fromkeys(CONTABO_NAMES, "x"))
    )

    assert isinstance(provider, ContaboProvider)
    assert "http://127.0.0.1:1/token" in repr(provider)


def test_authentication_url_is_not_read_from_the_environment() -> None:
    stream = io.StringIO()
    hostile = {
        "IAC_NODE_FORGE_CONTABO_AUTH_URL": "http://evil.example/token",
        "CONTABO_AUTH_URL": "http://evil.example/token",
    }
    services = build_services(hostile, stream, stream)

    provider = services.provider_factory(
        ProviderKind.CONTABO, Credentials(dict.fromkeys(CONTABO_NAMES, "x"))
    )

    assert "evil.example" not in repr(provider)
    assert contabo_module.DEFAULT_AUTH_URL in repr(provider)


def test_composition_builds_proxmox_for_a_single_token() -> None:
    stream = io.StringIO()
    services = build_services({}, stream, stream)

    provider = services.provider_factory(
        ProviderKind.PROXMOX_VE, Credentials.single("x")
    )

    assert isinstance(provider, ProxmoxProvider)


def _save_with_secret(
    harness: Harness, configuration: ProviderConfiguration, secret: str
) -> None:
    reference = CredentialReference(f"{configuration.display_name}-ref", "keyring")
    values = dict.fromkeys(configuration.provider_kind.credential_names, secret)
    harness.credential_store.put(reference, Credentials(values))
    harness.configurations.save(replace(configuration, credential_reference=reference))
