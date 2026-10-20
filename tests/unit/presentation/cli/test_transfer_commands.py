"""``server access``, ``server backup`` and ``server restore`` with fakes."""

from pathlib import Path

import pytest

from iac_node_forge.presentation.cli.commands.server_access import is_access_request
from iac_node_forge.presentation.cli.commands.server_backup import is_backup_request
from iac_node_forge.presentation.cli.commands.server_power import is_power_request
from iac_node_forge.presentation.cli.commands.server_remove import is_remove_request
from iac_node_forge.presentation.cli.commands.server_restore import is_restore_request
from tests.unit.application.fakes import FakeProvider
from tests.unit.application.samples import LAB, SECRET
from tests.unit.application.transfer_fakes import FakeRemoteHost
from tests.unit.application.use_cases.transfer_setup import (
    DB,
    DEPLOY,
    MANAGED_WEB,
    WEB,
)
from tests.unit.presentation.cli.harness import CliRun, Harness


def prepared(
    *,
    managed: bool = True,
    access: bool = True,
    interactive: bool = False,
    answers: tuple[str, ...] = (),
    host: FakeRemoteHost | None = None,
) -> Harness:
    harness = Harness(interactive=interactive, answers=answers)
    harness.provider = FakeProvider(accepted_secret=SECRET, servers=(WEB, DB))
    harness.configurations.save(LAB)
    if managed:
        harness.configurations.managed = (MANAGED_WEB,)
    harness.transfer.host = host or FakeRemoteHost()
    harness.transfer.host.add_file("/srv/app/a.txt", b"alpha")
    if access:
        harness.transfer.accesses.save(DEPLOY)
    return harness


def local_tree(tmp_path: Path) -> str:
    folder = tmp_path / "app"
    folder.mkdir()
    (folder / "a.txt").write_bytes(b"new alpha")
    return str(folder)


def pushes(harness: Harness) -> int:
    return [c.operation for c in harness.transfer.host.calls].count("push")


def test_the_verbs_are_told_apart_from_the_other_server_commands() -> None:
    assert is_access_request(["server", "access", "Lab", "100", "--user", "u"])
    assert is_backup_request(["server", "backup", "Lab", "100", "/x", "--to", "d"])
    assert is_restore_request(["server", "restore", "--help"])
    assert not is_access_request(["server", "access", "Lab"])
    assert not is_backup_request(["server", "backup", "Lab"])
    assert not is_restore_request(["server", "Lab", "restore"])
    assert not is_remove_request(["server", "backup", "Lab", "100"])
    assert not is_power_request(["server", "restore", "Lab", "100"])


def test_server_access_records_and_shows_the_facts_without_a_secret() -> None:
    harness = prepared(access=False)

    result = harness.run(
        "server", "access", "Lab", "100", "--user", "deploy", "--port", "2222"
    )

    assert result.code == 0, result.err
    assert "User:" in result.out and "deploy" in result.out
    assert "2222" in result.out
    saved = harness.transfer.accesses.load(LAB.configuration_id, "100")
    assert saved is not None and saved.port == 2222


def test_server_access_refuses_key_material_and_does_not_echo_it() -> None:
    harness = prepared(access=False)
    key = "-----BEGIN OPENSSH PRIVATE KEY-----zzz"

    result = harness.run(
        "server", "access", "Lab", "100", "--user", "u", "--identity", key
    )

    assert result.code == 1
    assert "zzz" not in result.out + result.err
    assert harness.transfer.accesses.saved == {}


def test_server_access_needs_a_user() -> None:
    assert prepared().run("server", "access", "Lab", "100").code == 2


def test_a_backup_shows_the_preview_then_copies_and_reports(tmp_path: Path) -> None:
    harness = prepared()

    result = harness.run(
        "server", "backup", "Lab", "100", "/srv/app", "--to", str(tmp_path / "o")
    )

    assert result.code == 0, result.err
    for label in ("Connect:", "Host key:", "Size:", "Free:", "Required:", "Report:"):
        assert label in result.out
    assert "Nothing on the server is changed" in result.out
    assert "Copied and verified" in result.out
    assert (tmp_path / "o" / "srv/app/a.txt").read_bytes() == b"alpha"
    assert harness.transfer.host.writes_on_server == []


def test_a_backup_of_an_unknown_host_key_prints_the_fingerprint_and_the_rerun_hint(
    tmp_path: Path,
) -> None:
    harness = prepared(host=FakeRemoteHost(start_known=False))
    fingerprint = harness.transfer.host.key.fingerprint
    argv = ("server", "backup", "Lab", "100", "/srv/app", "--to", str(tmp_path / "o"))

    stopped = harness.run(*argv)
    accepted = harness.run(*argv, "--accept-host-key", fingerprint)

    assert stopped.code == 1
    assert fingerprint in stopped.out
    assert f"--accept-host-key {fingerprint}" in stopped.err
    assert accepted.code == 0, accepted.err


def test_a_wrong_fingerprint_is_refused(tmp_path: Path) -> None:
    harness = prepared(host=FakeRemoteHost(start_known=False))

    result = harness.run(
        "server", "backup", "Lab", "100", "/srv/app", "--to", str(tmp_path / "o"),
        "--accept-host-key", "SHA256:wrong",
    )  # fmt: skip

    assert result.code == 1
    assert "does not match" in result.err
    assert harness.transfer.host.known == {}


def test_a_backup_without_access_points_to_server_access(tmp_path: Path) -> None:
    result = prepared(access=False).run(
        "server", "backup", "Lab", "100", "/srv/app", "--to", str(tmp_path / "o")
    )

    assert result.code == 1
    assert "server access Lab 100 --user" in result.err


def test_a_restore_lists_the_overwrites_and_needs_yes_without_a_terminal(
    tmp_path: Path,
) -> None:
    harness = prepared()
    source = local_tree(tmp_path)

    refused = harness.run("server", "restore", "Lab", "100", source, "--to", "/srv")

    assert refused.code == 1
    assert "/srv/app" in refused.out
    assert "NOT kept" in refused.out
    assert "--yes" in refused.err
    assert pushes(harness) == 0


def test_a_restore_with_yes_replaces_and_reports(tmp_path: Path) -> None:
    harness = prepared()

    result = harness.run(
        "server", "restore", "Lab", "100", local_tree(tmp_path), "--to", "/srv",
        "--yes", "--report-dir", str(tmp_path / "reports"),
    )  # fmt: skip

    assert result.code == 0, result.err
    assert harness.transfer.host.content("/srv/app/a.txt") == b"new alpha"
    assert "Replaced:" in result.out
    assert list((tmp_path / "reports").glob("*.json"))


def test_an_unmanaged_server_needs_the_second_confirmation(tmp_path: Path) -> None:
    harness = prepared(managed=False)
    source = local_tree(tmp_path)
    base: tuple[str, ...] = (
        "server",
        "restore",
        "Lab",
        "100",
        source,
        "--to",
        "/srv",
        "--yes",
    )
    base += ("--report-dir", str(tmp_path / "r"))

    first = harness.run(*base)
    wrong = harness.run(*base, "--confirm-unmanaged", "101")
    done = harness.run(*base, "--confirm-unmanaged", "100")

    assert first.code == 1 and "--confirm-unmanaged 100" in first.err
    assert wrong.code == 1 and "does not match" in wrong.err
    assert done.code == 0, done.err


def test_at_a_terminal_the_identifier_is_typed_and_a_name_is_refused(
    tmp_path: Path,
) -> None:
    harness = prepared(interactive=True, answers=("web",))
    source = local_tree(tmp_path)
    argv: tuple[str, ...] = ("server", "restore", "Lab", "100", source, "--to", "/srv")
    argv += ("--report-dir", str(tmp_path / "r"))

    wrong = harness.run(*argv)
    assert pushes(harness) == 0
    harness.answers = ("100",)
    right = harness.run(*argv)

    assert wrong.code == 1 and "does not match" in wrong.err
    assert pushes(harness) == 1
    assert right.code == 0, right.err
    assert "Type the server identifier '100'" in harness.asked[0]


@pytest.mark.parametrize("verb", ["backup", "restore"])
def test_a_server_that_is_not_running_is_refused(verb: str, tmp_path: Path) -> None:
    harness = prepared()
    harness.transfer.accesses.save(
        DEPLOY.__class__(DEPLOY.configuration_id, "101", "192.0.2.10", 22, "u", None)
    )
    target = ("--to", str(tmp_path / "o")) if verb == "backup" else ("--to", "/srv")
    path = "/srv/app" if verb == "backup" else local_tree(tmp_path)

    result: CliRun = harness.run("server", verb, "Lab", "101", path, *target, "--yes")

    assert result.code == 1
    assert "stopped" in result.err
    assert pushes(harness) == 0
