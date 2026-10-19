"""OpenSshCommands: the only place that builds command lines (ADR-0009)."""

import re
import shlex
from pathlib import Path

import pytest

from iac_node_forge.domain.errors import InvalidRemotePathError
from iac_node_forge.domain.server_access import RemoteAccess
from iac_node_forge.infrastructure.openssh.commands import (
    ALLOWED_REMOTE_PROGRAMS,
    READ_PROGRAMS,
    WRITE_PROGRAMS,
    OpenSshCommands,
    host_spec,
    tar_names,
)

ACCESS = RemoteAccess("web.example.org", 2222, "deploy", None)
AWKWARD = '/srv/my data/it\'s; $(id) `x` "q" & *'
FORBIDDEN_WORDS = ("rm", "mv", "cp", "chmod", "chown", "sh", "bash", "eval", "xargs")


def reads(paths: list[str]) -> list[str]:
    commands = OpenSshCommands
    return [
        commands.tar_create(paths),
        commands.tar_owner_probe(),
        commands.disk_usage(paths),
        commands.find_hashes(paths),
        commands.find_sizes(paths),
        commands.find_links(paths),
        commands.stat_paths(paths),
    ]


def writes(destination: str) -> list[str]:
    return [
        OpenSshCommands.make_directory_and_extract(destination, True),
        OpenSshCommands.make_directory_and_extract(destination, False),
    ]


def test_ssh_is_a_list_of_arguments_ending_with_the_host_and_one_command() -> None:
    argv = OpenSshCommands().ssh(ACCESS, "du -sk -- /srv")

    assert isinstance(argv, list)
    assert argv[-3:] == ["--", "web.example.org", "du -sk -- /srv"]
    assert argv[0] == "ssh"


def test_ssh_checks_the_host_key_strictly_and_sets_port_user_and_timeout() -> None:
    argv = OpenSshCommands(connect_timeout=7).ssh(ACCESS, "true")

    options = [argv[i + 1] for i, part in enumerate(argv) if part == "-o"]
    assert "StrictHostKeyChecking=yes" in options
    assert "ConnectTimeout=7" in options
    assert argv[argv.index("-p") + 1] == "2222"
    assert argv[argv.index("-l") + 1] == "deploy"
    assert "-T" in argv


def test_ssh_never_forwards_or_accepts_keys_silently() -> None:
    argv = " ".join(OpenSshCommands().ssh(ACCESS, "true"))

    assert "accept-new" not in argv
    assert "StrictHostKeyChecking=no" not in argv
    assert "ForwardAgent=no" in argv


def test_a_run_without_a_terminal_fails_instead_of_waiting() -> None:
    quiet = OpenSshCommands(interactive=False).ssh(ACCESS, "true")
    asking = OpenSshCommands(interactive=True).ssh(ACCESS, "true")

    assert "BatchMode=yes" in quiet
    assert "BatchMode=yes" not in asking


def test_the_identity_hint_is_passed_as_i_only_when_it_names_an_existing_file(
    tmp_path: Path,
) -> None:
    key = tmp_path / "id_test"
    key.write_text("not a real key", encoding="utf-8")
    commands = OpenSshCommands()

    with_file = commands.ssh(RemoteAccess("h", 22, "u", str(key)), "true")
    with_note = commands.ssh(RemoteAccess("h", 22, "u", "the team key"), "true")
    without = commands.ssh(RemoteAccess("h", 22, "u", None), "true")

    assert with_file[with_file.index("-i") + 1] == str(key)
    assert "IdentitiesOnly=yes" in with_file
    assert "-i" not in with_note
    assert "-i" not in without


def test_no_command_line_holds_a_password_or_a_key() -> None:
    commands = OpenSshCommands()
    argv = [
        *commands.ssh(ACCESS, "true"),
        *commands.keyscan("h", 22),
        *commands.fingerprints(),
        *commands.known_entries("h", 22),
    ]

    assert not [a for a in argv if re.search("pass|secret|token|BEGIN", a, re.I)]


def test_extra_options_are_written_name_equals_value() -> None:
    with pytest.raises(ValueError):
        OpenSshCommands(extra_options=("-oProxyCommand=x",))
    with pytest.raises(ValueError):
        OpenSshCommands(extra_options=("NoEquals",))

    argv = OpenSshCommands(extra_options=("IdentityAgent=none",)).ssh(ACCESS, "true")

    assert "IdentityAgent=none" in argv


def test_known_hosts_names_a_non_default_port_in_brackets() -> None:
    assert host_spec("h", 22) == "h"
    assert host_spec("h", 2222) == "[h]:2222"
    assert OpenSshCommands().known_entries("h", 2222)[:3] == [
        "ssh-keygen",
        "-F",
        "[h]:2222",
    ]


def test_the_keyscan_asks_for_the_modern_key_types_with_a_timeout() -> None:
    argv = OpenSshCommands(connect_timeout=9).keyscan("h", 22)

    assert argv[argv.index("-T") + 1] == "9"
    assert argv[-2:] == ["--", "h"]


def test_every_read_template_quotes_every_path() -> None:
    for command in reads([AWKWARD, "/etc/nginx"]):
        if command == OpenSshCommands.tar_owner_probe():
            continue  # it names no path
        words = shlex.split(command)
        assert sum(AWKWARD.lstrip("/") in w for w in words) == 1, command


def test_the_destination_of_a_restore_is_quoted_and_follows_dash_dash() -> None:
    command = OpenSshCommands.make_directory_and_extract(AWKWARD, False)

    mkdir, tar = command.split(" && ")
    assert shlex.split(mkdir) == ["mkdir", "-p", "--", AWKWARD]
    assert shlex.split(tar) == ["tar", "-xf", "-", "-C", AWKWARD]


def test_tar_create_places_paths_after_dash_dash_without_a_leading_slash() -> None:
    words = shlex.split(OpenSshCommands.tar_create(["/etc/nginx", "/var/www"]))

    assert words == ["tar", "-cf", "-", "-C", "/", "--", "etc/nginx", "var/www"]
    assert tar_names(["/etc/nginx"]) == ["etc/nginx"]


@pytest.mark.parametrize(
    "path", ["/a\nb", "/a\x00b", "/a\rb", "relative", "/a/../b", "/a//b", "/a/", ""]
)
def test_a_path_with_a_nul_a_line_break_or_dots_is_refused_by_every_template(
    path: str,
) -> None:
    for build in (
        OpenSshCommands.tar_create,
        OpenSshCommands.disk_usage,
        OpenSshCommands.find_hashes,
        OpenSshCommands.find_sizes,
        OpenSshCommands.find_links,
        OpenSshCommands.stat_paths,
    ):
        with pytest.raises(InvalidRemotePathError):
            build([path])
    with pytest.raises(InvalidRemotePathError):
        OpenSshCommands.make_directory_and_extract(path, False)


def test_no_path_at_all_and_the_root_are_refused_for_a_backup() -> None:
    with pytest.raises(InvalidRemotePathError):
        OpenSshCommands.tar_create([])
    with pytest.raises(InvalidRemotePathError):
        OpenSshCommands.tar_create(["/"])


def test_the_remote_programs_are_the_allow_list_of_the_adr() -> None:
    assert ALLOWED_REMOTE_PROGRAMS == {
        "tar",
        "find",
        "sha256sum",
        "stat",
        "readlink",
        "du",
        "mkdir",
    }
    assert WRITE_PROGRAMS == {"tar", "mkdir"}
    assert "mkdir" not in READ_PROGRAMS


def test_a_backup_runs_only_read_programs_and_no_redirection() -> None:
    for command in reads(["/etc/nginx", AWKWARD]):
        for segment in command.split(" && "):
            words = shlex.split(segment)
            assert words[0] in READ_PROGRAMS, command
        assert not set(shlex.split(command)) & set(FORBIDDEN_WORDS), command
        assert not re.search(r"(?<!['\"])[<>|`$]", _unquoted(command)), command


def test_find_may_exec_only_sha256sum_stat_or_readlink() -> None:
    for command in reads(["/etc/nginx"]):
        words = shlex.split(command)
        for index, word in enumerate(words):
            if word == "-exec":
                assert words[index + 1] in {"sha256sum", "stat", "readlink"}
        assert "-delete" not in words and "-ok" not in words


def test_a_restore_adds_only_mkdir_and_tar_extract() -> None:
    for command in writes("/srv/in"):
        programs = [shlex.split(s)[0] for s in command.split(" && ")]
        assert programs == ["mkdir", "tar"]
        assert set(programs) <= WRITE_PROGRAMS
        assert "-xf" in shlex.split(command)


def test_no_owner_flag_is_added_only_when_asked() -> None:
    with_flag, without = writes("/srv/in")

    assert "--no-same-owner" in shlex.split(with_flag)
    assert "--no-same-owner" not in shlex.split(without)


def _unquoted(command: str) -> str:
    """Return the command with every quoted word removed."""
    return " ".join(
        word if shlex.quote(word) == word else "Q" for word in shlex.split(command)
    )
