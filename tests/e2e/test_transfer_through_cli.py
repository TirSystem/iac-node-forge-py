"""Backing up and restoring files end to end: real composition root, mock provider.

The provider side is the mock Proxmox VE API and the real access store in the
configuration file; the server behind SSH is the in-process fake, so nothing
runs ssh and nothing touches the user's real ``~/.ssh``. They show the
acceptance criteria of US-001.09 (back up server files) and US-001.10 (restore
files) and the Go criteria of MIL-007: a backup writes nothing on the server,
nothing is copied to the server before the typed confirmation, one changed byte
is a failure, the host key rules hold and no secret is stored or shown.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from iac_node_forge.domain.managed_server_record import ManagedServerRecord
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.infrastructure.configuration import (
    JsonLinesConfigurationStore,
    JsonLinesServerAccessStore,
)
from iac_node_forge.presentation.cli.services import TransferServices
from tests.contract.conftest import TOKEN
from tests.e2e.conftest import MultiProviderSession, Result
from tests.unit.application.transfer_fakes import (
    FakeLocalFilesystem,
    FakeRemoteHost,
)

RUNNING = "100"
STOPPED = "101"
ADDRESS = "192.0.2.10"
SEEDED_AT = datetime(2026, 1, 1, tzinfo=UTC)
KEY_MARKER = "PRIVATE KEY"


@dataclass(slots=True)
class Lab:
    """A logged-in session, the fake server behind SSH and a local work folder."""

    session: MultiProviderSession
    host: FakeRemoteHost
    work: Path
    filesystem: FakeLocalFilesystem

    def run(self, *argv: str) -> Result:
        return self.session.run(*argv)

    def define_access(self, server: str = RUNNING) -> Result:
        return self.run(
            "server", "access", "Lab", server, "--user", "deploy", "--host", ADDRESS
        )

    def backup(self, *extra: str, server: str = RUNNING) -> Result:
        return self.run(
            "server", "backup", "Lab", server, "/srv/app", "--to", str(self.out), *extra
        )

    def restore(self, *extra: str, server: str = RUNNING) -> Result:
        return self.run(
            "server", "restore", "Lab", server, str(self.source), "--to", "/srv",
            "--report-dir", str(self.reports), *extra,
        )  # fmt: skip

    def manage(self, server: str = RUNNING) -> None:
        [row] = self.file_rows("provider_configuration")
        JsonLinesConfigurationStore(
            self.session.configuration_file
        ).save_managed_server(
            ManagedServerRecord(
                ConfigurationId(str(row["configuration_id"])), server, SEEDED_AT
            )
        )

    def file_rows(self, record_type: str) -> list[dict[str, object]]:
        text = self.session.configuration_file.read_text("utf-8")
        rows = [json.loads(line) for line in text.splitlines()]
        return [r for r in rows if r["record_type"] == record_type]

    @property
    def out(self) -> Path:
        return self.work / "backup"

    @property
    def reports(self) -> Path:
        return self.work / "reports"

    @property
    def source(self) -> Path:
        return self.work / "app"

    def pushes(self) -> int:
        return [c.operation for c in self.host.calls].count("push")

    def all_text(self) -> str:
        """Everything a secret could hide in: records, reports and outputs."""
        parts = [self.session.configuration_file.read_text("utf-8")]
        for folder in (self.out, self.reports):
            if folder.exists():
                parts += [p.read_text("utf-8") for p in folder.rglob("*.json")]
        return "\n".join(parts)


def make_lab(
    session: MultiProviderSession, tmp_path: Path, host: FakeRemoteHost | None = None
) -> Lab:
    fake = host or FakeRemoteHost()
    if host is None or not fake.nodes:
        fake.add_file("/srv/app/a.txt", b"alpha")
        fake.add_file("/srv/app/sub/b.txt", b"beta")
    filesystem = FakeLocalFilesystem()
    session.transfer = TransferServices(
        server_access=JsonLinesServerAccessStore(session.configuration_file),
        remote_reader=fake,
        file_transfer=fake,
        known_hosts=fake,
        local_filesystem=filesystem,
    )
    added = session.run(
        "provider", "add", "--address", session.proxmox.address, "--name", "Lab"
    )
    assert added.code == 0, added.err
    work = tmp_path / "work"
    (work / "app" / "sub").mkdir(parents=True)
    (work / "app" / "a.txt").write_bytes(b"new alpha")
    (work / "app" / "sub" / "b.txt").write_bytes(b"new beta")
    return Lab(session, fake, work, filesystem)


@pytest.fixture
def lab(multi_session: MultiProviderSession, tmp_path: Path) -> Lab:
    made = make_lab(multi_session, tmp_path)
    assert made.define_access().code == 0
    return made


def test_a_backup_is_copied_verified_and_changes_nothing_on_the_server(
    lab: Lab,
) -> None:
    """US-001.09: the data is copied and verified complete; the server is unchanged."""
    before = {path: lab.host.content(path) for path in lab.host.nodes}

    result = lab.backup()

    assert result.code == 0, result.err
    assert "Copied and verified" in result.out
    assert "Nothing on the server is changed" in result.out
    assert (lab.out / "srv/app/a.txt").read_bytes() == b"alpha"
    assert (lab.out / "srv/app/sub/b.txt").read_bytes() == b"beta"
    assert list(lab.out.glob("*.json"))
    assert lab.host.writes_on_server == []
    assert {path: lab.host.content(path) for path in lab.host.nodes} == before
    assert lab.session.proxmox.write_requests == []


def test_a_destination_without_room_is_refused_with_the_reason(lab: Lab) -> None:
    """US-001.09: a destination without enough room is refused with the reason."""
    lab.filesystem.free = 1000

    result = lab.backup()

    assert result.code == 1
    assert "1000 bytes free" in result.err
    assert "Required:" in result.out
    assert not lab.out.exists()
    assert "pull" not in [c.operation for c in lab.host.calls]


def test_a_restore_tells_what_is_overwritten_and_needs_a_confirmation_first(
    lab: Lab,
) -> None:
    """US-001.10: told what would be overwritten, must confirm before copying."""
    lab.manage()
    without_yes = lab.restore()
    lab.session.interactive = True
    lab.session.answers[:] = [""]
    refused = lab.restore()

    for result in (without_yes, refused):
        assert result.code == 1
        assert "/srv/app" in result.out
        assert "NOT kept" in result.out
    assert "--yes" in without_yes.err
    assert "not confirmed" in refused.err
    assert "Type the server identifier '100'" in lab.session.questions[-1]
    assert lab.pushes() == 0
    assert lab.host.content("/srv/app/a.txt") == b"alpha"


def test_a_completed_restore_is_verified_and_reported(lab: Lab) -> None:
    """US-001.10: a completed restore is verified complete and reported."""
    lab.manage()

    result = lab.restore("--yes")

    assert result.code == 0, result.err
    assert "Copied and verified" in result.out
    assert "Replaced:" in result.out
    assert lab.host.content("/srv/app/a.txt") == b"new alpha"
    assert lab.host.content("/srv/app/sub/b.txt") == b"new beta"
    [report_file] = lab.reports.glob("*.json")
    report = json.loads(report_file.read_text("utf-8"))
    assert report["verified"] is True
    assert report["direction"] == "restore"
    assert [o["path"] for o in report["overwritten"]] == ["/srv/app"]


@pytest.mark.parametrize("direction", ["backup", "restore"])
def test_one_changed_byte_is_a_failure_and_never_reported_as_done(
    multi_session: MultiProviderSession, tmp_path: Path, direction: str
) -> None:
    """MIL-007 Go criterion: a corrupted byte is verification failed, exit 1."""
    lab = make_lab(multi_session, tmp_path, FakeRemoteHost(corrupt_in_transit=True))
    assert lab.define_access().code == 0
    lab.manage()

    result = lab.backup() if direction == "backup" else lab.restore("--yes")

    assert result.code == 1
    assert "verification failed" in result.err
    assert "Copied and verified" not in result.out
    assert "Verified:" in result.out and "Difference:" in result.out


def test_an_unknown_host_key_stops_until_its_fingerprint_is_accepted(
    multi_session: MultiProviderSession, tmp_path: Path
) -> None:
    """MIL-007: unknown stops with the fingerprint; the exact one is accepted."""
    lab = make_lab(multi_session, tmp_path, FakeRemoteHost(start_known=False))
    assert lab.define_access().code == 0
    fingerprint = lab.host.key.fingerprint

    stopped = lab.backup()
    wrong = lab.backup("--accept-host-key", "SHA256:notthekey")
    still_unknown = dict(lab.host.known)
    accepted = lab.backup("--accept-host-key", fingerprint)

    assert stopped.code == 1
    assert fingerprint in stopped.out
    assert f"--accept-host-key {fingerprint}" in stopped.err
    assert wrong.code == 1 and "does not match" in wrong.err
    assert still_unknown == {}
    assert accepted.code == 0, accepted.err
    assert lab.host.known == {(ADDRESS, 22): fingerprint}
    assert lab.backup("--accept-host-key", fingerprint).code == 1  # not empty now


def test_a_changed_host_key_stops_and_is_never_accepted(
    multi_session: MultiProviderSession, tmp_path: Path
) -> None:
    """MIL-007: a changed key stops, even with its fingerprint given."""
    fake = FakeRemoteHost()
    fake.change_key()
    fake.known[(ADDRESS, 22)] = "SHA256:serverkeyone"
    lab = make_lab(multi_session, tmp_path, fake)
    assert lab.define_access().code == 0

    plain = lab.backup()
    forced = lab.backup("--accept-host-key", fake.key.fingerprint)
    restored = lab.restore("--yes", "--accept-host-key", fake.key.fingerprint)

    for result in (plain, forced, restored):
        assert result.code == 1
        assert "differs from the one in known_hosts" in result.err
        assert "never accepted" in result.err
    assert fake.known == {(ADDRESS, 22): "SHA256:serverkeyone"}
    assert lab.pushes() == 0


def test_an_unmanaged_server_needs_the_extra_confirmation(lab: Lab) -> None:
    """US-001.10 and MIL-007: an unmanaged server needs the identifier twice."""
    without_extra = lab.restore("--yes")
    wrong_extra = lab.restore("--yes", "--confirm-unmanaged", "999")

    assert without_extra.code == 1
    assert "not managed by this client" in without_extra.out
    assert "--confirm-unmanaged 100" in without_extra.err
    assert wrong_extra.code == 1 and "does not match" in wrong_extra.err
    assert lab.pushes() == 0

    given = lab.restore("--yes", "--confirm-unmanaged", RUNNING)

    assert given.code == 0, given.err
    assert lab.pushes() == 1


def test_a_name_or_a_prefix_is_not_the_identifier(lab: Lab) -> None:
    """MIL-007 Go criterion: only the exact identifier confirms; nothing is copied."""
    lab.manage()
    lab.session.interactive = True

    for typed in ("web", "10", "x100"):
        lab.session.answers[:] = [typed]
        result = lab.restore()
        assert result.code == 1
        assert "does not match" in result.err

    assert lab.pushes() == 0
    assert lab.host.content("/srv/app/a.txt") == b"alpha"


def test_a_server_that_is_not_running_is_refused_for_both_directions(
    lab: Lab,
) -> None:
    """MIL-007: a stopped server is neither read nor written."""
    assert lab.define_access(STOPPED).code == 0

    backup = lab.backup(server=STOPPED)
    restore = lab.restore("--yes", server=STOPPED)

    for result in (backup, restore):
        assert result.code == 1
        assert "stopped" in result.err
    assert lab.host.calls == []


def test_no_secret_is_stored_or_shown(
    multi_session: MultiProviderSession, tmp_path: Path
) -> None:
    """MIL-007 Go criterion: no password, key or token in records, reports or output."""
    lab = make_lab(multi_session, tmp_path, FakeRemoteHost(start_known=False))
    key = f"-----BEGIN OPENSSH {KEY_MARKER}-----SECRETBYTES"
    refused = lab.run(
        "server", "access", "Lab", RUNNING, "--user", "deploy", "--host", ADDRESS,
        "--identity", key,
    )  # fmt: skip
    assert lab.define_access().code == 0
    lab.manage()
    fingerprint = lab.host.key.fingerprint

    results = [
        refused,
        lab.backup(),
        lab.backup("--accept-host-key", fingerprint),
        lab.restore("--yes"),
        lab.restore("--yes", "--confirm-unmanaged", "x"),
    ]

    assert refused.code == 1
    shown = "\n".join(r.out + r.err for r in results) + lab.all_text()
    secrets = (TOKEN, TOKEN.split("=", 1)[1], "SECRETBYTES", KEY_MARKER)
    for secret in secrets:
        assert secret not in shown
    [access] = lab.file_rows("server_access")
    assert set(access) >= {"host", "port", "user"}
    assert not {"password", "passphrase", "key", "token", "secret"} & set(access)
