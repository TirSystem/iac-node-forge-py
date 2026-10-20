"""Live file transfer through the real CLI against a throw-away sshd container.

The commands run through the real entry point with the real OpenSSH adapters,
the real local disk and the real access store. The provider side is the
in-process mock Proxmox VE API; the server behind SSH is the container of
``tests/integration/sshd`` on 127.0.0.1:2222. The ssh commands use a throw-away
key, a temporary known_hosts and a temporary ``-F`` config, so the user's own
``~/.ssh`` is never read or written.

Opt-in: ``IACNF_SSHD_TEST=1`` and a working Docker (see the README).
"""

import io
import json
import re
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from iac_node_forge.infrastructure.configuration import JsonLinesServerAccessStore
from iac_node_forge.infrastructure.filesystem import LocalDiskFilesystem
from iac_node_forge.infrastructure.openssh import (
    OpenSshFileTransfer,
    OpenSshKnownHosts,
    OpenSshRemoteReader,
)
from iac_node_forge.presentation.cli.services import TransferServices
from tests.contract.conftest import TOKEN
from tests.e2e.conftest import (  # noqa: F401 - fixtures used by name
    MultiProviderSession,
    Result,
    memory_keyring,
    mock_contabo,
    mock_proxmox,
    multi_session,
)
from tests.integration import sshd_server
from tests.integration.sshd_server import SshdServer
from tests.openssh_sandbox import isolated_commands

pytestmark = pytest.mark.integration

SERVER = "100"  # a running server of the Proxmox mock
REMOTE_APP = f"{sshd_server.REMOTE_ROOT}/app"
FINGERPRINT = re.compile(r"SHA256:[A-Za-z0-9+/=]+")

type Tree = dict[str, tuple[str, bytes | str | None]]


@pytest.fixture
def sshd(tmp_path: Path) -> Iterator[SshdServer]:
    """The throw-away container with a new key; removed after the test."""
    reason = sshd_server.unavailable_reason()
    if reason is not None:
        pytest.skip(reason)
    directory = tmp_path / "sshd"
    directory.mkdir()
    with sshd_server.running_sshd(directory) as server:
        yield server


@dataclass(slots=True)
class Live:
    """The CLI session wired to the real adapters, and the sandbox files."""

    session: MultiProviderSession
    server: SshdServer
    known_hosts: Path
    work: Path

    def run(self, *argv: str) -> Result:
        return self.session.run(*argv)

    def backup(self, *extra: str, to: Path | None = None) -> Result:
        return self.run(
            "server", "backup", "Lab", SERVER, REMOTE_APP,
            "--to", str(to or self.backup_dir), *extra,
        )  # fmt: skip

    def restore(self, *extra: str) -> Result:
        return self.run(
            "server", "restore", "Lab", SERVER, str(self.backup_dir / REMOTE_APP[1:]),
            "--to", sshd_server.REMOTE_ROOT, "--report-dir", str(self.reports), *extra,
        )  # fmt: skip

    @property
    def backup_dir(self) -> Path:
        return self.work / "backup"

    @property
    def reports(self) -> Path:
        return self.work / "reports"

    @property
    def local_app(self) -> Path:
        return self.backup_dir / REMOTE_APP[1:]


@pytest.fixture
def live(
    multi_session: MultiProviderSession,  # noqa: F811 - imported fixture
    sshd: SshdServer,
    tmp_path: Path,
) -> Live:
    sandbox = tmp_path / "ssh"
    sandbox.mkdir()
    commands = isolated_commands(sandbox)
    multi_session.transfer = TransferServices(
        server_access=JsonLinesServerAccessStore(multi_session.configuration_file),
        remote_reader=OpenSshRemoteReader(commands),
        file_transfer=OpenSshFileTransfer(commands, timeout=180),
        known_hosts=OpenSshKnownHosts(commands),
        local_filesystem=LocalDiskFilesystem(),
    )
    work = tmp_path / "work"
    work.mkdir()
    assert commands.known_hosts_file is not None
    return Live(multi_session, sshd, commands.known_hosts_file, work)


def seed_remote() -> None:
    """Empty the work folder of the container and put a small tree in it."""
    sshd_server.exec_in_container(
        [
            "sh",
            "-c",
            f"mkdir -p {sshd_server.REMOTE_ROOT} && cd {sshd_server.REMOTE_ROOT} "
            "&& rm -rf ./* ./.[!.]*",
        ]
    )
    files = {
        "app/a.txt": b"alpha\n",
        "app/sub/deep/b.bin": bytes(range(256)) * 20,
        "app/empty.txt": b"",
        "app/nåve-ünï.txt": "café".encode(),
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.mode, info.size = 0o644, len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("app/link-to-a")
        link.type, link.linkname, link.mode = tarfile.SYMTYPE, "a.txt", 0o777
        tar.addfile(link)
    sshd_server.exec_in_container(
        ["sh", "-c", f"cd {sshd_server.REMOTE_ROOT} && tar -xf -"], buffer.getvalue()
    )


def remote_tree() -> Tree:
    """Return the manifest and bytes of the remote ``app`` tree."""
    packed = sshd_server.exec_in_container(
        ["tar", "-cf", "-", "-C", sshd_server.REMOTE_ROOT, "app"]
    ).stdout
    tree: Tree = {}
    with tarfile.open(fileobj=io.BytesIO(packed), mode="r") as tar:
        for member in tar.getmembers():
            name = member.name.rstrip("/")
            if member.issym():
                tree[name] = ("link", member.linkname)
            elif member.isdir():
                tree[name] = ("dir", None)
            else:
                handle = tar.extractfile(member)
                assert handle is not None
                tree[name] = ("file", handle.read())
    return tree


def local_tree(base: Path) -> Tree:
    """Return the manifest and bytes of a local ``app`` folder, as remote_tree."""
    tree: Tree = {"app": ("dir", None)}
    for path in sorted(base.rglob("*")):
        name = "app/" + path.relative_to(base).as_posix()
        if path.is_symlink():
            tree[name] = ("link", path.readlink().as_posix())
        elif path.is_dir():
            tree[name] = ("dir", None)
        else:
            tree[name] = ("file", path.read_bytes())
    return tree


def sandbox_text(live: Live) -> str:
    """Everything a secret could hide in: the record and every report."""
    parts = [live.session.configuration_file.read_text("utf-8")]
    for folder in (live.backup_dir, live.reports):
        parts += [p.read_text("utf-8") for p in folder.rglob("*.json")]
    return "\n".join(parts)


def test_a_backup_and_a_restore_round_trip_through_the_real_cli(live: Live) -> None:
    """MIL-007: the whole path against a real sshd, step by step."""
    outputs: list[Result] = []

    def run(result: Result) -> Result:
        outputs.append(result)
        return result

    address = live.session.proxmox.address
    added = run(live.run("provider", "add", "--address", address, "--name", "Lab"))
    assert added.code == 0, added.err

    # 1) the access is saved with the throw-away key as the identity hint
    saved = run(
        live.run(
            "server",
            "access",
            "Lab",
            SERVER,
            "--user",
            live.server.user,
            "--host",
            live.server.host,
            "--port",
            str(live.server.port),
            "--identity",
            str(live.server.key),
        )  # fmt: skip
    )
    assert saved.code == 0, saved.err
    [access] = [
        row
        for row in map(
            json.loads, live.session.configuration_file.read_text("utf-8").splitlines()
        )
        if row["record_type"] == "server_access"
    ]
    assert (access["host"], access["port"], access["user"]) == (
        live.server.host,
        live.server.port,
        live.server.user,
    )

    # 2) a small tree in the container
    seed_remote()
    before = remote_tree()
    assert before["app/empty.txt"] == ("file", b"")
    assert before["app/link-to-a"] == ("link", "a.txt")
    assert len(before) == 8

    # 3) an unknown host key stops the backup and copies nothing
    stopped = run(live.backup())
    assert stopped.code != 0
    printed = FINGERPRINT.findall(stopped.out + stopped.err)
    assert printed, "the fingerprint is printed"
    fingerprint = printed[-1]
    assert not live.backup_dir.exists()
    assert not live.known_hosts.exists() or live.known_hosts.read_text("utf-8") == ""

    # 4) the exact fingerprint accepts it; the copy equals the source
    done = run(live.backup("--accept-host-key", fingerprint))
    assert done.code == 0, done.err + done.out
    assert "Copied and verified" in done.out
    assert live.known_hosts.read_text("utf-8").strip()
    assert local_tree(live.local_app) == before
    assert list(live.backup_dir.glob("*.json")), "the report is in the destination"
    assert remote_tree() == before

    # 5) the remote tree changes: one edited, one deleted, one added
    sshd_server.exec_in_container(
        [
            "sh",
            "-c",
            f"cd {REMOTE_APP} && printf changed > a.txt && rm sub/deep/b.bin "
            "&& printf added > added.txt",
        ]
    )
    changed = remote_tree()
    assert changed != before

    # 6) restore: the preview names what is replaced; nothing is written unconfirmed
    without_yes = run(live.restore())
    assert without_yes.code == 1
    assert REMOTE_APP in without_yes.out
    assert "Replace" in without_yes.out
    assert "--yes" in without_yes.err
    live.session.interactive = True
    live.session.answers[:] = [""]
    refused = run(live.restore())
    assert refused.code == 1
    assert remote_tree() == changed
    live.session.answers[:] = [SERVER, SERVER]
    restored = run(live.restore())
    # ADR-0009: the restore copies over the folder and deletes nothing, and any
    # extra file makes the verification fail. So the added file is reported as
    # the one difference, and everything else is back as in the backup.
    assert restored.code == 1, restored.err + restored.out
    assert "verification failed" in restored.err
    assert "app/added.txt (extra)" in restored.err + restored.out
    assert "Copied and verified" not in restored.out
    assert remote_tree() == {**before, "app/added.txt": ("file", b"added")}
    [report_file] = live.reports.glob("*.json")
    report = json.loads(report_file.read_text("utf-8"))
    assert report["verified"] is False
    assert [d["path"] for d in report["differences"]] == ["app/added.txt"]
    live.session.interactive = False

    # 7) a changed host key stops, and accepting the fingerprint does not override it
    other = sshd_server.make_key_pair(live.work, "id_other")
    key_type, key_body = sshd_server.public_key_line(other).split()[:2]
    live.known_hosts.write_text(
        f"[{live.server.host}]:{live.server.port} {key_type} {key_body}\n", "utf-8"
    )
    pinned = live.known_hosts.read_text("utf-8")
    for extra in ((), ("--accept-host-key", fingerprint)):
        changed_key = run(live.backup(*extra, to=live.work / "second"))
        assert changed_key.code != 0
        assert "differs from the one in known_hosts" in changed_key.err
        assert "never accepted" in changed_key.err
    assert live.known_hosts.read_text("utf-8") == pinned
    assert not (live.work / "second").exists()

    # 8) no secret in any output, the access record or the reports
    shown = "\n".join(r.out + r.err for r in outputs) + sandbox_text(live)
    private = live.server.key.read_text("utf-8")
    secrets = [TOKEN, TOKEN.split("=", 1)[1], "PRIVATE KEY"]
    secrets += [line for line in private.splitlines() if len(line) > 20]
    for secret in secrets:
        assert secret not in shown
    assert not {"password", "passphrase", "key", "token", "secret"} & set(access)
