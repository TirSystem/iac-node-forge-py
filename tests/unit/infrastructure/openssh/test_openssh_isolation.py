"""Guard: the test configuration of OpenSSH cannot resolve to the real ~/.ssh.

The developer's machine has real keys and a real known_hosts. A test that runs
ssh, ssh-keyscan or ssh-keygen must be built with ``isolated_commands``.
"""

from pathlib import Path

from iac_node_forge.domain.server_access import RemoteAccess
from tests.openssh_sandbox import isolated_commands

REAL_SSH = Path.home() / ".ssh"
ACCESS = RemoteAccess("127.0.0.1", 2222, "tester", None)


def _every_argument(directory: Path) -> list[str]:
    commands = isolated_commands(directory)
    return [
        *commands.ssh(ACCESS, "true"),
        *commands.keyscan("127.0.0.1", 2222),
        *commands.fingerprints(),
        *commands.known_entries("127.0.0.1", 2222),
    ]


def test_the_test_configuration_is_isolated(tmp_path: Path) -> None:
    assert isolated_commands(tmp_path).is_isolated


def test_the_default_configuration_is_the_users_own() -> None:
    from iac_node_forge.infrastructure.openssh import OpenSshCommands

    assert not OpenSshCommands().is_isolated


def test_no_argument_names_the_real_ssh_directory(tmp_path: Path) -> None:
    for argument in _every_argument(tmp_path):
        assert str(REAL_SSH).lower() not in argument.lower()
        assert "~/.ssh" not in argument
        assert ".ssh" not in argument


def test_ssh_reads_only_files_under_the_temporary_directory(tmp_path: Path) -> None:
    argv = isolated_commands(tmp_path).ssh(ACCESS, "true")
    options = [argv[i + 1] for i, part in enumerate(argv) if part == "-o"]

    files = {
        "-F": argv[argv.index("-F") + 1],
        "known": _value(options, "UserKnownHostsFile"),
        "global": _value(options, "GlobalKnownHostsFile"),
    }

    for name, file in files.items():
        assert Path(file).is_relative_to(tmp_path), name
    assert "IdentityAgent=none" in options
    assert "IdentitiesOnly=yes" in options


def test_the_known_hosts_lookup_names_the_temporary_file(tmp_path: Path) -> None:
    argv = isolated_commands(tmp_path).known_entries("127.0.0.1", 2222)

    assert Path(argv[argv.index("-f") + 1]) == tmp_path / "known_hosts"


def _value(options: list[str], name: str) -> str:
    return next(o.split("=", 1)[1] for o in options if o.startswith(name + "="))
