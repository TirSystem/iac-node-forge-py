"""The reader, the host key check and the known_hosts write, with scripted tools.

The OpenSSH programs are replaced by canned answers, so these tests run no
program and need no network; the real tools run in the contract suite.
"""

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from iac_node_forge.application.errors import (
    ConnectionFailedError,
    HostKeyChangedError,
    HostKeyUnknownError,
    PermissionDeniedError,
    RemotePathNotFoundError,
    TransferError,
    TransferFailedError,
)
from iac_node_forge.domain.host_key import HostKey, HostKeyStanding
from iac_node_forge.domain.server_access import RemoteAccess
from iac_node_forge.infrastructure.openssh import (
    OpenSshCommands,
    OpenSshKnownHosts,
    OpenSshRemoteReader,
    _process,
)
from iac_node_forge.infrastructure.openssh._process import Completed

ACCESS = RemoteAccess("web.example.org", 22, "deploy", None)
HASH = "ab" * 32
KEY_A = "web.example.org ssh-ed25519 AAAAkeyA"
KEY_B = "web.example.org ssh-rsa AAAAkeyB"


def ok(stdout: str = "", stderr: str = "") -> Completed:
    return Completed(0, stdout.encode("utf-8"), stderr)


def failed(returncode: int, stderr: str, stdout: str = "") -> Completed:
    return Completed(returncode, stdout.encode("utf-8"), stderr)


class Tools:
    """Canned answers by what the command line contains; records the calls.

    An answer may be a list: its items are served in turn, the last one again.
    """

    def __init__(self, *answers: tuple[str, Completed | list[Completed]]) -> None:
        self.answers = list(answers)
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: Sequence[str], *, stdin: bytes | None = None, timeout: float = 0
    ) -> Completed:
        self.calls.append(list(argv))
        line = " ".join(argv)
        for needle, answer in self.answers:
            if needle in line:
                if isinstance(answer, list):
                    return answer.pop(0) if len(answer) > 1 else answer[0]
                return answer
        raise AssertionError(f"unexpected command: {line}")


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Tools]:
    def _install(*answers: tuple[str, Completed | list[Completed]]) -> Tools:
        tools = Tools(*answers)
        monkeypatch.setattr(_process, "run", tools)
        return tools

    return _install


def test_total_size_sums_the_kibibytes_of_du(install: Callable[..., Tools]) -> None:
    install(("du -sk", ok("10\t/srv/a\n5\t/srv/b\n")))

    assert OpenSshRemoteReader().total_size(ACCESS, ["/srv/a", "/srv/b"]) == 15 * 1024


def test_total_size_of_a_missing_path_is_path_not_found(
    install: Callable[..., Tools],
) -> None:
    install(("du -sk", failed(1, "du: cannot access '/x': No such file or directory")))

    with pytest.raises(RemotePathNotFoundError):
        OpenSshRemoteReader().total_size(ACCESS, ["/x"])


def test_total_size_with_unreadable_output_fails(install: Callable[..., Tools]) -> None:
    install(("du -sk", ok("garbage\n")))

    with pytest.raises(TransferFailedError):
        OpenSshRemoteReader().total_size(ACCESS, ["/x"])


def test_the_manifest_joins_hashes_sizes_and_links(
    install: Callable[..., Tools],
) -> None:
    tools = install(
        ("sha256sum", ok(f"{HASH}  /srv/app/a.txt\n")),
        ("stat -c", ok("5 /srv/app/a.txt\n")),
        ("-type l", ok("/srv/app/l\na.txt\n")),
    )

    manifest = OpenSshRemoteReader().manifest(ACCESS, "/srv", ["app"])

    assert [(e.path, e.kind.value) for e in manifest.entries] == [
        ("app/a.txt", "file"),
        ("app/l", "link"),
    ]
    assert len(tools.calls) == 3
    assert all(call[-1].startswith("find ") for call in tools.calls)


def test_the_manifest_of_a_missing_path_is_path_not_found(
    install: Callable[..., Tools],
) -> None:
    install(("find", failed(1, "find: '/srv/x': No such file or directory")))

    with pytest.raises(RemotePathNotFoundError):
        OpenSshRemoteReader().manifest(ACCESS, "/srv", ["x"])


def test_the_manifest_of_an_unreadable_directory_is_permission_denied(
    install: Callable[..., Tools],
) -> None:
    install(("find", failed(1, "find: '/srv/x': Permission denied")))

    with pytest.raises(PermissionDeniedError):
        OpenSshRemoteReader().manifest(ACCESS, "/srv", ["x"])


def test_existing_returns_what_exists_and_ignores_what_is_missing(
    install: Callable[..., Tools],
) -> None:
    install(
        (
            "stat -c",
            failed(
                1,
                "stat: cannot statx 'b': No such file or directory\n",
                "5 1700000000 /srv/a\n",
            ),
        )
    )

    found = OpenSshRemoteReader().existing(ACCESS, "/srv", ["a", "b"])

    assert [(e.path, e.size) for e in found] == [("/srv/a", 5)]
    assert found[0].modified_at.tzinfo is not None


def test_existing_fails_on_an_error_that_is_not_a_missing_path(
    install: Callable[..., Tools],
) -> None:
    install(("stat -c", failed(1, "stat: /srv/a: Permission denied")))

    with pytest.raises(PermissionDeniedError):
        OpenSshRemoteReader().existing(ACCESS, "/srv", ["a"])


def test_existing_asks_in_batches_so_a_command_line_stays_short(
    install: Callable[..., Tools],
) -> None:
    tools = install(("stat -c", ok("")))

    OpenSshRemoteReader().existing(ACCESS, "/srv", [f"f{n}" for n in range(250)])

    assert len(tools.calls) == 3


@pytest.mark.parametrize(
    ("completed", "expected"),
    [
        (
            failed(255, "ssh: connect to host h port 22: Connection refused"),
            ConnectionFailedError,
        ),
        (failed(255, "ssh: Could not resolve hostname h"), ConnectionFailedError),
        (
            failed(255, "Connection timed out during banner exchange"),
            ConnectionFailedError,
        ),
        (
            failed(255, "deploy@h: Permission denied (publickey,password)."),
            PermissionDeniedError,
        ),
        (failed(255, "Host key verification failed."), HostKeyUnknownError),
        (
            failed(
                255,
                "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!\n"
                "Host key verification failed.",
            ),
            HostKeyChangedError,
        ),
        (failed(1, "tar: x: Cannot open: Permission denied"), PermissionDeniedError),
        (failed(1, "tar: x: No such file or directory"), RemotePathNotFoundError),
        (failed(2, "tar: something odd"), TransferFailedError),
        (failed(255, "something nobody expected"), ConnectionFailedError),
    ],
)
def test_ssh_failures_map_to_the_transfer_errors(
    completed: Completed, expected: type[TransferError]
) -> None:
    assert type(_process.failure(completed, "tar")) is expected


def test_a_line_that_looks_like_a_secret_is_withheld_from_the_error() -> None:
    stderr = "debug: password is hunter2\nBad packet length"

    error = _process.failure(failed(255, stderr), "ssh")

    assert "hunter2" not in str(error) and "password" not in str(error)
    assert "Bad packet length" in str(error)


def test_an_error_text_is_bounded() -> None:
    error = _process.failure(failed(255, "x" * 5000), "ssh")

    assert len(str(error)) < 400


def test_a_program_that_is_missing_is_a_connection_failure() -> None:
    with pytest.raises(ConnectionFailedError):
        _process.run(["iacnf-no-such-program-xyz"])


def test_a_command_that_runs_too_long_is_stopped() -> None:
    with pytest.raises(ConnectionFailedError):
        _process.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)


def test_a_streamed_command_is_killed_at_its_timeout() -> None:
    code = "import time; time.sleep(30)"
    with _process.StreamedProcess([sys.executable, "-c", code], timeout=0.5) as p:
        p.wait_for_end(10)
        completed = p.finish()

    assert completed.stderr.startswith("timed out after")
    assert isinstance(_process.failure(completed, "tar"), TransferFailedError)


def prints(*values: str) -> Completed:
    return ok("".join(f"256 {v} host (KEY)\n" for v in values))


def test_a_host_with_no_entry_is_unknown_and_shows_the_preferred_key(
    install: Callable[..., Tools],
) -> None:
    install(
        ("ssh-keyscan", ok(f"{KEY_B}\n{KEY_A}\n")),
        ("ssh-keygen -F", failed(1, "")),
        ("ssh-keygen -l", prints("SHA256:b", "SHA256:a")),
    )

    check = OpenSshRemoteReader().check_host_key(ACCESS)

    assert check.standing is HostKeyStanding.UNKNOWN
    assert check.host_key is not None
    assert (check.host_key.key_type, check.host_key.fingerprint) == (
        "ssh-ed25519",
        "SHA256:a",
    )


def test_a_scanned_key_that_matches_an_entry_is_known(
    install: Callable[..., Tools],
) -> None:
    install(
        ("ssh-keyscan", ok(f"{KEY_A}\n{KEY_B}\n")),
        ("ssh-keygen -F", ok(f"# Host x found: line 1\n{KEY_B}\n")),
        ("ssh-keygen -l", [prints("SHA256:a", "SHA256:b"), prints("SHA256:b")]),
    )

    check = OpenSshRemoteReader().check_host_key(ACCESS)

    assert check.standing is HostKeyStanding.KNOWN
    assert check.host_key is not None and check.host_key.fingerprint == "SHA256:b"


def test_an_entry_that_matches_no_scanned_key_is_changed(
    install: Callable[..., Tools],
) -> None:
    install(
        ("ssh-keyscan", ok(f"{KEY_A}\n")),
        ("ssh-keygen -F", ok(f"{KEY_B}\n")),
        ("ssh-keygen -l", [prints("SHA256:a"), prints("SHA256:old")]),
    )

    check = OpenSshRemoteReader().check_host_key(ACCESS)

    assert check.standing is HostKeyStanding.CHANGED


def test_a_server_that_shows_no_key_is_a_failed_connection(
    install: Callable[..., Tools],
) -> None:
    install(("ssh-keyscan", ok("# web:22 SSH-2.0-OpenSSH\n")))

    with pytest.raises(ConnectionFailedError):
        OpenSshRemoteReader().check_host_key(ACCESS)


def test_the_host_key_check_runs_nothing_on_the_server(
    install: Callable[..., Tools],
) -> None:
    tools = install(
        ("ssh-keyscan", ok(f"{KEY_A}\n")),
        ("ssh-keygen -F", failed(1, "")),
        ("ssh-keygen -l", prints("SHA256:a")),
    )

    OpenSshRemoteReader().check_host_key(ACCESS)

    assert [call[0] for call in tools.calls] == [
        "ssh-keyscan",
        "ssh-keygen",
        "ssh-keygen",
    ]


def trust_setup(
    install: Callable[..., Tools], tmp_path: Path, entries: Completed
) -> tuple[OpenSshKnownHosts, Path, HostKey]:
    hosts = tmp_path / "known_hosts"
    install(
        ("ssh-keyscan", ok(f"{KEY_A}\n")),
        ("ssh-keygen -F", entries),
        ("ssh-keygen -l", prints("SHA256:a")),
    )
    commands = OpenSshCommands(known_hosts_file=hosts)
    return (
        OpenSshKnownHosts(commands),
        hosts,
        HostKey("web.example.org", 22, "ssh-ed25519", "SHA256:a"),
    )


def test_trust_appends_one_line_to_the_known_hosts_file_only(
    install: Callable[..., Tools], tmp_path: Path
) -> None:
    trust, hosts, key = trust_setup(install, tmp_path, failed(1, ""))
    hosts.write_text("old.example.org ssh-rsa AAAAold\n", encoding="utf-8")

    trust.trust(key)

    assert hosts.read_text("utf-8") == (
        "old.example.org ssh-rsa AAAAold\nweb.example.org ssh-ed25519 AAAAkeyA\n"
    )


def test_trust_starts_a_new_line_when_the_file_does_not_end_with_one(
    install: Callable[..., Tools], tmp_path: Path
) -> None:
    trust, hosts, key = trust_setup(install, tmp_path, failed(1, ""))
    hosts.write_text("old ssh-rsa AAAAold", encoding="utf-8")

    trust.trust(key)

    assert hosts.read_text("utf-8").splitlines() == [
        "old ssh-rsa AAAAold",
        "web.example.org ssh-ed25519 AAAAkeyA",
    ]


def test_trust_names_a_non_default_port_in_brackets(
    install: Callable[..., Tools], tmp_path: Path
) -> None:
    trust, hosts, _ = trust_setup(install, tmp_path, failed(1, ""))

    trust.trust(HostKey("web.example.org", 2222, "ssh-ed25519", "SHA256:a"))

    assert hosts.read_text("utf-8") == "[web.example.org]:2222 ssh-ed25519 AAAAkeyA\n"


def test_trust_creates_the_file_and_its_directory_when_absent(
    install: Callable[..., Tools], tmp_path: Path
) -> None:
    hosts = tmp_path / "dir" / "known_hosts"
    install(
        ("ssh-keyscan", ok(f"{KEY_A}\n")),
        ("ssh-keygen -F", failed(1, "")),
        ("ssh-keygen -l", prints("SHA256:a")),
    )
    trust = OpenSshKnownHosts(OpenSshCommands(known_hosts_file=hosts))

    trust.trust(HostKey("web.example.org", 22, "ssh-ed25519", "SHA256:a"))

    assert hosts.read_text("utf-8").count("\n") == 1


def test_trust_never_touches_a_host_that_already_has_an_entry(
    install: Callable[..., Tools], tmp_path: Path
) -> None:
    trust, hosts, key = trust_setup(install, tmp_path, ok(f"{KEY_B}\n"))
    hosts.write_text(f"{KEY_B}\n", encoding="utf-8")

    with pytest.raises(HostKeyChangedError):
        trust.trust(key)

    assert hosts.read_text("utf-8") == f"{KEY_B}\n"


def test_trust_refuses_a_fingerprint_the_server_no_longer_shows(
    install: Callable[..., Tools], tmp_path: Path
) -> None:
    trust, hosts, _ = trust_setup(install, tmp_path, failed(1, ""))

    with pytest.raises(HostKeyUnknownError):
        trust.trust(HostKey("web.example.org", 22, "ssh-ed25519", "SHA256:other"))

    assert not hosts.exists()
