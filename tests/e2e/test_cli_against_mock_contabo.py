"""The command-line client end to end: real adapters, mock Contabo API.

Each test shows one acceptance criterion of US-001.01, US-001.02 or US-001.06
with ``--kind contabo``, or one Go criterion of MIL-003 (4: only GET and the
token request are sent; 5: no credential in the configuration file or in an
error text).
"""

import json
import socket

from tests.contract.conftest import CONTABO_VALUES, CONTABO_WRONG_VALUES
from tests.contract.mock_contabo import TOKEN_PATH
from tests.e2e.conftest import MultiProviderSession, reference_name

SECRET_VALUES = tuple(CONTABO_VALUES.values())
KINDS_OF_CONTABO = "valid kinds: region, image"


def add_contabo(session: MultiProviderSession, *extra: str) -> None:
    result = session.run(
        "provider",
        "add",
        "--kind",
        "contabo",
        "--address",
        session.contabo.address,
        "--name",
        "Cloud",
        *extra,
    )
    assert result.code == 0, result.err


def unused_address() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def assert_no_secret(text: str) -> None:
    for value in SECRET_VALUES + (CONTABO_WRONG_VALUES["api_password"],):
        assert value not in text


def test_provider_add_then_read_commands_work_end_to_end(
    multi_session: MultiProviderSession,
) -> None:
    """US-001.01, US-001.02, US-001.06: the whole read-only slice for Contabo."""
    add = multi_session.run(
        "provider",
        "add",
        "--kind",
        "contabo",
        "--address",
        multi_session.contabo.address,
        "--name",
        "Cloud",
        "--cache-credential",
    )
    capabilities = multi_session.run("capabilities", "Cloud")
    regions = multi_session.run("options", "Cloud", "region")
    servers = multi_session.run("servers")
    server = multi_session.run("server", "Cloud", "100")

    assert add.code == 0, add.err
    assert f"Access to {multi_session.contabo.address} works" in add.out
    assert capabilities.code == regions.code == servers.code == server.code == 0
    assert "EU1" in regions.out
    assert "web" in servers.out
    assert "2 vCPU, 2048 MiB" in server.out
    assert multi_session.prompted_names == list(CONTABO_VALUES)


def test_provider_add_with_a_wrong_password_saves_nothing(
    multi_session: MultiProviderSession,
) -> None:
    """US-001.01: invalid credentials: the reason is stated, nothing is saved."""
    multi_session.secrets.update(CONTABO_WRONG_VALUES)

    result = multi_session.run(
        "provider",
        "add",
        "--kind",
        "contabo",
        "--address",
        multi_session.contabo.address,
        "--name",
        "Cloud",
    )

    assert result.code == 1
    assert "token request failed" in result.err
    assert "client ID, client secret, API user and API password" in result.err
    assert not multi_session.configuration_file.exists()
    assert_no_secret(result.out + result.err)


def test_provider_add_for_an_unreachable_contabo_is_reported(
    multi_session: MultiProviderSession,
) -> None:
    """US-001.01: unreachable provider: stated, nothing is saved."""
    result = multi_session.run(
        "provider",
        "add",
        "--kind",
        "contabo",
        "--address",
        unused_address(),
        "--name",
        "Gone",
    )

    assert result.code == 1
    assert "could not be reached" in result.err
    assert "Traceback" not in result.err
    assert not multi_session.configuration_file.exists()
    assert_no_secret(result.out + result.err)


def test_capabilities_show_supported_and_unsupported_actions(
    multi_session: MultiProviderSession,
) -> None:
    """US-001.02: supported actions are listed, others shown as unsupported."""
    add_contabo(multi_session)

    result = multi_session.run("capabilities", "Cloud")

    status = {line.split()[0]: line.split()[1] for line in result.out.splitlines()[2:]}
    assert "(contabo)" in result.out.splitlines()[0]
    assert status["list_servers"] == "supported"
    assert status["inspect_server"] == "supported"
    assert status["provision_server"] == "unsupported"
    assert status["delete_server"] == "unsupported"


def test_options_list_regions_and_images_of_the_provider(
    multi_session: MultiProviderSession,
) -> None:
    """US-001.02: the data centers and the images of the provider are listed."""
    add_contabo(multi_session)

    regions = multi_session.run("options", "Cloud", "region")
    images = multi_session.run("options", "Cloud", "image")

    assert regions.out.splitlines()[1:] == ["  EU1", "  US-east"]
    assert len(images.out.splitlines()[1:]) == 2
    assert "3f1c0a52-0000-4000-8000-000000000001" in images.out


def test_options_of_a_proxmox_kind_fail_for_contabo_and_list_valid_kinds(
    multi_session: MultiProviderSession,
) -> None:
    """US-001.02: an unknown kind exits 1 and names the provider's kinds."""
    add_contabo(multi_session)

    result = multi_session.run("options", "Cloud", "node")

    assert result.code == 1
    assert KINDS_OF_CONTABO in result.err
    assert "'node'" in result.err


def test_servers_show_provider_state_size_addresses_and_not_managed(
    multi_session: MultiProviderSession,
) -> None:
    """US-001.06: each server shows its data; unknown servers are not managed."""
    add_contabo(multi_session)

    result = multi_session.run("servers")

    web = next(line for line in result.out.splitlines() if " web " in line)
    for text in ("Cloud", "100", "running", "2 vCPU, 2048 MiB", "203.0.113.10"):
        assert text in web
    assert all(line.endswith("not managed") for line in result.out.splitlines()[1:])


def test_server_shows_the_details_and_not_found_exits_one(
    multi_session: MultiProviderSession,
) -> None:
    """US-001.06: details of one server; an absent server is stated."""
    add_contabo(multi_session)

    found = multi_session.run("server", "Cloud", "101")
    missing = multi_session.run("server", "Cloud", "999")

    assert found.code == 0
    assert "db" in found.out
    assert "203.0.113.20" in found.out
    assert missing.code == 1
    assert "999" in missing.err


def test_servers_of_both_providers_are_listed_and_a_failure_does_not_hide_them(
    multi_session: MultiProviderSession,
) -> None:
    """US-001.06: Proxmox VE and Contabo together; one failing is reported."""
    add_contabo(multi_session)
    proxmox = multi_session.run(
        "provider",
        "add",
        "--address",
        multi_session.proxmox.address,
        "--name",
        "Lab",
    )
    assert proxmox.code == 0, proxmox.err

    both = multi_session.run("servers")
    multi_session.contabo.state.forced_status = 500
    degraded = multi_session.run("servers")

    assert both.code == 0
    providers = {line.split()[0] for line in both.out.splitlines()[1:]}
    assert providers == {"Cloud", "Lab"}
    assert degraded.code == 1
    assert "Lab" in degraded.out
    assert "Cloud" not in degraded.out
    assert "provider 'Cloud' (Contabo) could not be read" in degraded.err
    assert "Traceback" not in degraded.err


def test_no_credential_is_written_to_the_configuration_file(
    multi_session: MultiProviderSession,
) -> None:
    """MIL-003 Go criterion 5: nothing in the file; four values in the keyring."""
    add_contabo(multi_session, "--cache-credential")

    text = multi_session.configuration_file.read_text(encoding="utf-8")

    assert_no_secret(text)
    assert "credential_reference" in text
    stored = multi_session.keyring.get_password("iac-node-forge", reference_name(text))
    assert json.loads(stored or "") == CONTABO_VALUES


def test_no_credential_appears_in_an_error_text(
    multi_session: MultiProviderSession,
) -> None:
    """MIL-003 Go criterion 5: rejected credentials do not leak into output."""
    add_contabo(multi_session, "--cache-credential")
    multi_session.contabo.state.api_password = "rotated-at-the-provider"

    servers = multi_session.run("servers")
    server = multi_session.run("server", "Cloud", "100")

    assert servers.code == server.code == 1
    assert "provider 'Cloud' (Contabo) could not be read (rejected)" in servers.err
    assert "rejected the client ID" in server.err
    assert_no_secret(servers.out + servers.err + server.out + server.err)


def test_cached_credentials_are_used_without_asking_again(
    multi_session: MultiProviderSession,
) -> None:
    add_contabo(multi_session, "--cache-credential")
    asked_before = len(multi_session.prompted_names)

    result = multi_session.run("servers")

    assert result.code == 0
    assert len(multi_session.prompted_names) == asked_before


def test_corrupt_keyring_entry_is_ignored_with_one_line_and_asked_again(
    multi_session: MultiProviderSession,
) -> None:
    add_contabo(multi_session, "--cache-credential")
    name = reference_name(multi_session.configuration_file.read_text(encoding="utf-8"))
    multi_session.keyring.set_password("iac-node-forge", name, "{not json")
    asked_before = len(multi_session.prompted_names)

    result = multi_session.run("servers")

    assert result.code == 0
    assert result.err.count("\n") == 1
    assert name in result.err
    assert "ignored" in result.err
    assert "not json" not in result.err
    assert len(multi_session.prompted_names) == asked_before + 4


def test_only_get_and_the_token_post_reach_the_provider(
    multi_session: MultiProviderSession,
) -> None:
    """MIL-003 Go criterion 4: no create, update, delete or action request."""
    add_contabo(multi_session, "--cache-credential")
    for command in (
        ("capabilities", "Cloud"),
        ("options", "Cloud", "region"),
        ("options", "Cloud", "image"),
        ("servers",),
        ("server", "Cloud", "101"),
    ):
        assert multi_session.run(*command).code == 0

    contabo = multi_session.contabo
    assert contabo.api_requests
    assert {r.method for r in contabo.api_requests} == {"GET"}
    assert {(r.method, r.path) for r in contabo.token_requests} == {
        ("POST", TOKEN_PATH)
    }
    assert contabo.methods_used == {"GET", "POST"}
