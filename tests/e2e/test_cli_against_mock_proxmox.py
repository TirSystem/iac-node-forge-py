"""The command-line client end to end: real adapters, mock Proxmox VE API.

Each test shows one acceptance criterion of US-001.01, US-001.02 or US-001.06,
or one Go criterion of MIL-002 (5: no secret in the configuration file; 6: no
create, update or delete call).
"""

import json
import socket

from tests.contract.conftest import TOKEN, WRONG_TOKEN
from tests.e2e.conftest import CliSession, reference_name

SECRET_PART = TOKEN.split("=", 1)[1]


def add_provider(session: CliSession, *extra: str) -> None:
    result = session.run(
        "provider",
        "add",
        "--address",
        session.server.address,
        "--name",
        "Lab",
        *extra,
    )
    assert result.code == 0, result.err


def unused_address() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def test_provider_add_then_read_commands_work_end_to_end(
    cli_session: CliSession,
) -> None:
    """US-001.01, US-001.02, US-001.06: the whole read-only slice."""
    add = cli_session.run(
        "provider",
        "add",
        "--address",
        cli_session.server.address,
        "--name",
        "Lab",
        "--cache-credential",
    )
    capabilities = cli_session.run("capabilities", "Lab")
    nodes = cli_session.run("options", "Lab", "node")
    servers = cli_session.run("servers")
    server = cli_session.run("server", "Lab", "100")

    assert add.code == 0
    assert f"Access to {cli_session.server.address} works." in add.out
    assert capabilities.code == nodes.code == servers.code == server.code == 0
    assert "pve1" in nodes.out
    assert "pve2" in nodes.out
    assert "web" in servers.out
    assert "db" in servers.out
    assert "2 vCPU, 2048 MiB" in server.out


def test_provider_add_with_a_rejected_token_saves_nothing(
    cli_session: CliSession,
) -> None:
    """US-001.01: invalid credentials: the reason is stated, nothing is saved."""
    cli_session.token = WRONG_TOKEN

    result = cli_session.run(
        "provider", "add", "--address", cli_session.server.address, "--name", "Lab"
    )

    assert result.code == 1
    assert "token rejected" in result.err
    assert not cli_session.configuration_file.exists()
    assert WRONG_TOKEN not in result.out + result.err


def test_capabilities_show_supported_and_unsupported_actions(
    cli_session: CliSession,
) -> None:
    """US-001.02: supported actions are listed, others shown as unsupported."""
    add_provider(cli_session)

    result = cli_session.run("capabilities", "Lab")

    status = {line.split()[0]: line.split()[1] for line in result.out.splitlines()[2:]}
    assert status["list_servers"] == "supported"
    assert status["inspect_server"] == "supported"
    assert status["provision_server"] == "unsupported"
    assert status["delete_server"] == "unsupported"


def test_options_list_nodes_and_storage_of_the_provider(
    cli_session: CliSession,
) -> None:
    """US-001.02: the nodes and the storage of the provider are listed."""
    add_provider(cli_session)

    nodes = cli_session.run("options", "Lab", "node")
    storage = cli_session.run("options", "Lab", "storage")

    assert nodes.out.splitlines()[1:] == ["  pve1", "  pve2"]
    assert storage.out.splitlines()[1:] == [
        "  pve1/local",
        "  pve1/local-lvm",
        "  pve2/local",
    ]


def test_options_of_an_unknown_kind_fail_with_exit_one(
    cli_session: CliSession,
) -> None:
    add_provider(cli_session)

    result = cli_session.run("options", "Lab", "flavour")

    assert result.code == 1
    assert "flavour" in result.err


def test_servers_show_provider_state_size_and_not_managed_marking(
    cli_session: CliSession,
) -> None:
    """US-001.06: each server shows its data; unknown servers are not managed."""
    add_provider(cli_session)

    result = cli_session.run("servers")

    web = next(line for line in result.out.splitlines() if " web " in line)
    for text in ("Lab", "100", "running", "2 vCPU, 2048 MiB"):
        assert text in web
    assert all(line.endswith("not managed") for line in result.out.splitlines()[1:])


def test_server_not_found_exits_one(cli_session: CliSession) -> None:
    add_provider(cli_session)

    result = cli_session.run("server", "Lab", "999")

    assert result.code == 1
    assert "999" in result.err


def test_no_secret_is_written_to_the_configuration_file(
    cli_session: CliSession,
) -> None:
    """MIL-002 Go criterion 5: no secret in the configuration file."""
    add_provider(cli_session, "--cache-credential")

    text = cli_session.configuration_file.read_text(encoding="utf-8")

    assert TOKEN not in text
    assert SECRET_PART not in text
    assert "credential_reference" in text
    stored = cli_session.keyring.get_password("iac-node-forge", reference_name(text))
    assert json.loads(stored or "") == {"token": TOKEN}


def test_cached_token_is_used_without_asking_again(
    cli_session: CliSession,
) -> None:
    add_provider(cli_session, "--cache-credential")
    asked_before = cli_session.secret_prompts

    result = cli_session.run("servers")

    assert result.code == 0
    assert cli_session.secret_prompts == asked_before


def test_token_is_asked_again_when_it_was_not_cached(
    cli_session: CliSession,
) -> None:
    add_provider(cli_session)
    asked_before = cli_session.secret_prompts

    result = cli_session.run("servers")

    assert result.code == 0
    assert cli_session.secret_prompts == asked_before + 1


def test_only_get_requests_reach_the_provider(cli_session: CliSession) -> None:
    """MIL-002 Go criterion 6: no create, update or delete call."""
    add_provider(cli_session, "--cache-credential")
    for command in (
        ("capabilities", "Lab"),
        ("options", "Lab", "node"),
        ("options", "Lab", "storage"),
        ("servers",),
        ("server", "Lab", "101"),
    ):
        assert cli_session.run(*command).code == 0

    assert cli_session.server.state.requests
    assert cli_session.server.methods_used == {"GET"}


def test_unreachable_provider_is_reported_without_a_stack_trace(
    cli_session: CliSession,
) -> None:
    result = cli_session.run(
        "provider", "add", "--address", unused_address(), "--name", "Gone"
    )

    assert result.code == 1
    assert "could not be reached" in result.err
    assert "Traceback" not in result.err
    assert TOKEN not in result.out + result.err


def test_servers_report_an_unreachable_provider_without_a_stack_trace(
    cli_session: CliSession,
) -> None:
    add_provider(cli_session)
    file = cli_session.configuration_file
    file.write_text(
        file.read_text(encoding="utf-8").replace(
            cli_session.server.address, unused_address()
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = cli_session.run("servers")

    assert result.code == 1
    assert "provider 'Lab' (Proxmox VE) could not be read (unreachable)" in result.err
    assert "Traceback" not in result.err
