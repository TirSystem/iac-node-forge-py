"""Command-line behaviour with fakes: output, exit codes and failures.

The docstrings name the acceptance criterion of the user story each test shows.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from iac_node_forge.application.errors import (
    ProviderAuthenticationError,
    ProviderUnreachableError,
    UnknownOptionKindError,
)
from iac_node_forge.domain.capability import Capability
from iac_node_forge.domain.credential_reference import CredentialReference
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.server_option import ServerOption
from iac_node_forge.presentation.cli.main import main
from iac_node_forge.presentation.cli.settings import (
    VERIFY_ENV_VAR,
    SettingsError,
    read_tls_verification,
)
from tests.unit.application.fakes import FailingProvider, FakeProvider
from tests.unit.application.samples import (
    DB,
    EDGE,
    LAB,
    LAB_ID,
    SECRET,
    WEB,
    WEB_IS_MANAGED,
)
from tests.unit.presentation.cli.harness import Harness

ADD = ("provider", "add", "--address", "https://lab.example:8006", "--name", "Lab")


def with_labs(harness: Harness, *configurations: ProviderConfiguration) -> Harness:
    for configuration in configurations or (LAB,):
        harness.configurations.save(configuration)
    return harness


def test_provider_add_saves_and_states_that_access_works() -> None:
    """US-001.01: reachable provider, valid credentials: works and is saved."""
    harness = Harness()

    result = harness.run(*ADD)

    assert result.code == 0
    assert "Access to https://lab.example:8006 works." in result.out
    (saved,) = harness.configurations.load_all()
    assert saved.display_name == "Lab"
    assert saved.credential_reference is None
    assert SECRET not in result.out + result.err
    assert SECRET not in repr(saved)


def test_provider_add_states_the_reason_and_saves_nothing_for_a_bad_token() -> None:
    """US-001.01: invalid credentials: the reason is stated, nothing is saved."""
    harness = Harness()
    harness.prompt.secret = "not-the-token"

    result = harness.run(*ADD)

    assert result.code == 1
    assert "credentials rejected" in result.err
    assert "Nothing was saved" in result.err
    assert harness.configurations.load_all() == ()
    assert "not-the-token" not in result.out + result.err


def test_provider_add_asks_for_address_and_name_when_not_given() -> None:
    harness = Harness(answers=["https://lab.example:8006", "Lab"])

    result = harness.run("provider", "add")

    assert result.code == 0
    (saved,) = harness.configurations.load_all()
    assert (saved.address, saved.display_name) == ("https://lab.example:8006", "Lab")


def test_provider_add_fails_for_an_empty_address() -> None:
    harness = Harness(answers=["  ", "Lab"])

    result = harness.run("provider", "add")

    assert result.code == 1
    assert "must not be empty" in result.err
    assert harness.prompt.asked == []


def test_provider_add_fails_when_the_display_name_exists() -> None:
    harness = with_labs(Harness())

    result = harness.run(*ADD)

    assert result.code == 1
    assert "already exists" in result.err
    assert len(harness.configurations.load_all()) == 1


def test_provider_add_caches_the_token_when_asked() -> None:
    harness = Harness()

    result = harness.run(*ADD, "--cache-credential")

    assert result.code == 0
    assert "platform credential store" in result.out
    (saved,) = harness.configurations.load_all()
    assert saved.credential_reference is not None
    assert harness.credential_store.get(saved.credential_reference).secret == SECRET
    assert SECRET not in result.out + result.err


def test_provider_add_warns_and_succeeds_when_the_store_is_unavailable() -> None:
    harness = Harness()
    harness.credential_store.unavailable = True

    result = harness.run(*ADD, "--cache-credential")

    assert result.code == 0
    assert "warning: no usable platform credential store" in result.err
    (saved,) = harness.configurations.load_all()
    assert saved.credential_reference is None


def test_capabilities_list_supported_and_unsupported_actions() -> None:
    """US-001.02: supported actions are listed; the others show as unsupported."""
    harness = with_labs(
        Harness(
            provider=FakeProvider(capabilities=frozenset({Capability("list_servers")}))
        )
    )

    result = harness.run("capabilities", "Lab")

    assert result.code == 0
    assert "list_servers" in result.out
    lines = {line.split()[0]: line.split()[1] for line in result.out.splitlines()[2:]}
    assert lines["list_servers"] == "supported"
    assert lines["start_server"] == "unsupported"


def test_options_list_the_nodes_of_the_provider() -> None:
    """US-001.02: the nodes of the provider are listed."""
    harness = with_labs(
        Harness(
            provider=FakeProvider(
                options=(ServerOption("node", "pve1"), ServerOption("node", "pve2"))
            )
        )
    )

    result = harness.run("options", "Lab", "node")

    assert result.code == 0
    assert result.out.splitlines()[1:] == ["  pve1", "  pve2"]


def test_options_fail_with_exit_one_for_an_unknown_kind() -> None:
    harness = with_labs(
        Harness(provider=FailingProvider(UnknownOptionKindError("no kind 'x'")))
    )

    result = harness.run("options", "Lab", "x")

    assert result.code == 1
    assert "no kind 'x'" in result.err


def test_servers_show_provider_state_size_and_addresses() -> None:
    """US-001.06: each server shows provider, state, size and addresses."""
    harness = with_labs(Harness(provider=FakeProvider(servers=(WEB,))))

    result = harness.run("servers")

    assert result.code == 0
    row = result.out.splitlines()[1]
    for text in ("Lab", "running", "2 vCPU, 2048 MiB", "10.0.0.5"):
        assert text in row


def test_servers_mark_a_server_not_managed_by_this_client() -> None:
    """US-001.06: a server not created by this client is marked not managed."""
    harness = with_labs(Harness(provider=FakeProvider(servers=(WEB, DB))))
    harness.configurations.managed = (WEB_IS_MANAGED,)

    result = harness.run("servers")

    rows = {line.split()[1]: line for line in result.out.splitlines()[1:]}
    assert rows["101"].endswith("not managed")
    assert rows["100"].endswith("managed")
    assert not rows["100"].endswith("not managed")


def test_servers_show_the_others_when_one_provider_is_unreachable() -> None:
    harness = Harness(
        providers_by_secret={
            "lab-secret": FakeProvider(servers=(WEB,)),
            "edge-secret": FailingProvider(ProviderUnreachableError("no route")),
        }
    )
    for configuration, secret in ((LAB, "lab-secret"), (EDGE, "edge-secret")):
        reference = CredentialReference(
            f"{configuration.display_name}-token", "keyring"
        )
        harness.credential_store.put(reference, Credentials(secret))
        harness.configurations.save(
            replace(configuration, credential_reference=reference)
        )

    result = harness.run("servers")

    assert result.code == 1
    assert "web" in result.out
    assert "provider 'Edge' could not be read (unreachable): no route" in result.err
    assert "Traceback" not in result.err


def test_servers_fail_when_no_provider_is_configured() -> None:
    result = Harness().run("servers")

    assert result.code == 1
    assert "no provider is configured" in result.err
    assert "provider add" in result.err


def test_servers_fail_for_an_unknown_configuration() -> None:
    result = with_labs(Harness()).run("servers", "--configuration", "Nope")

    assert result.code == 1
    assert "'Nope'" in result.err


def test_server_shows_the_details() -> None:
    harness = with_labs(Harness(provider=FakeProvider(servers=(WEB,))))

    result = harness.run("server", "Lab", "100")

    assert result.code == 0
    assert "Name:" in result.out
    assert "web" in result.out
    assert "10.0.0.5" in result.out


def test_server_fails_with_exit_one_when_not_found() -> None:
    harness = with_labs(Harness(provider=FakeProvider(servers=(WEB,))))

    result = harness.run("server", "Lab", "999")

    assert result.code == 1
    assert "999" in result.err


def test_unreachable_provider_names_the_provider_and_hints() -> None:
    harness = with_labs(
        Harness(provider=FailingProvider(ProviderUnreachableError("timed out")))
    )

    result = harness.run("capabilities", "Lab")

    assert result.code == 1
    assert "provider 'Lab' could not be reached: timed out" in result.err
    assert "Hint: check the address and the network; for a self-signed" in result.err
    assert VERIFY_ENV_VAR in result.err
    assert "Traceback" not in result.err


def test_rejected_token_exits_one_with_a_hint() -> None:
    harness = with_labs(
        Harness(provider=FailingProvider(ProviderAuthenticationError("401")))
    )

    result = harness.run("server", "Lab", "100")

    assert result.code == 1
    assert "rejected the token" in result.err


def test_stored_credential_is_used_without_asking() -> None:
    reference = CredentialReference("lab-token", "keyring")
    harness = Harness()
    harness.configurations.save(
        ProviderConfiguration(LAB_ID, LAB.provider_kind, "Lab", LAB.address, reference)
    )
    harness.credential_store.put(reference, Credentials("stored-secret"))

    result = harness.run("servers")

    assert result.code == 0
    assert harness.secrets_used == ["stored-secret"]
    assert harness.prompt.asked == []


def test_prompt_is_used_when_the_credential_store_is_unavailable() -> None:
    reference = CredentialReference("lab-token", "keyring")
    harness = Harness()
    harness.configurations.save(
        ProviderConfiguration(LAB_ID, LAB.provider_kind, "Lab", LAB.address, reference)
    )
    harness.credential_store.unavailable = True

    result = harness.run("servers")

    assert result.code == 0
    assert harness.secrets_used == [SECRET]
    assert len(harness.prompt.asked) == 1


def test_prompt_is_used_when_the_store_has_no_secret() -> None:
    reference = CredentialReference("lab-token", "keyring")
    harness = Harness()
    harness.configurations.save(
        ProviderConfiguration(LAB_ID, LAB.provider_kind, "Lab", LAB.address, reference)
    )

    harness.run("servers")

    assert harness.secrets_used == [SECRET]


def test_missing_command_is_a_usage_error() -> None:
    result = Harness().run()

    assert result.code == 2
    assert "usage:" in result.err


def test_unknown_command_is_a_usage_error() -> None:
    result = Harness().run("destroy")

    assert result.code == 2
    assert "invalid choice" in result.err


def test_missing_argument_is_a_usage_error() -> None:
    result = Harness().run("server", "Lab")

    assert result.code == 2


def test_help_documents_the_environment_and_exit_codes() -> None:
    result = Harness().run("--help")

    assert result.code == 0
    for text in ("IAC_NODE_FORGE_CONFIG", VERIFY_ENV_VAR, "exit codes"):
        assert text in result.out


def test_provider_add_help_exits_zero() -> None:
    result = Harness().run("provider", "add", "--help")

    assert result.code == 0
    assert "--cache-credential" in result.out


def test_bad_verify_setting_exits_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(VERIFY_ENV_VAR, str(tmp_path / "missing.pem"))

    code = main(["servers"])

    assert code == 1
    assert VERIFY_ENV_VAR in capsys.readouterr().err


def test_verify_setting_defaults_to_true() -> None:
    assert read_tls_verification({}) is True
    assert read_tls_verification({VERIFY_ENV_VAR: "TRUE"}) is True


def test_verify_setting_can_be_false() -> None:
    assert read_tls_verification({VERIFY_ENV_VAR: "false"}) is False


def test_verify_setting_can_be_a_ca_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("pem", encoding="utf-8")

    assert read_tls_verification({VERIFY_ENV_VAR: str(bundle)}) == str(bundle)


def test_verify_setting_rejects_a_missing_bundle(tmp_path: Path) -> None:
    with pytest.raises(SettingsError):
        read_tls_verification({VERIFY_ENV_VAR: str(tmp_path / "nope.pem")})
