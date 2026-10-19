"""The transfer fakes against their ports; no network and no ssh."""

from pathlib import Path

import pytest

from iac_node_forge.application.errors import (
    ConnectionFailedError,
    HostKeyChangedError,
    HostKeyUnknownError,
    PermissionDeniedError,
    RemotePathNotFoundError,
)
from iac_node_forge.application.ports import (
    FileTransferPort,
    KnownHostsPort,
    LocalFilesystemPort,
    RemoteReadPort,
    ServerAccessStore,
)
from iac_node_forge.domain.host_key import HostKeyStanding
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.server_access import RemoteAccess, ServerAccess
from tests.unit.application.transfer_fakes import (
    OPERATIONS,
    FakeLocalFilesystem,
    FakeRemoteHost,
    FakeServerAccessStore,
    symlinks_supported,
)

ACCESS = RemoteAccess("192.0.2.10", 22, "deploy", None)


def host() -> FakeRemoteHost:
    fake = FakeRemoteHost()
    fake.add_file("/srv/app/a.txt", b"alpha")
    fake.add_file("/srv/app/sub/b.txt", b"beta")
    if symlinks_supported():
        fake.add_link("/srv/app/link", "a.txt")
    fake.add_special("/srv/app/tty", "device")
    return fake


def test_the_fakes_satisfy_their_ports() -> None:
    fake = FakeRemoteHost()

    ports: list[type] = [RemoteReadPort, FileTransferPort, KnownHostsPort]
    assert all(isinstance(fake, port) for port in ports)
    assert isinstance(FakeLocalFilesystem(), LocalFilesystemPort)
    assert isinstance(FakeServerAccessStore(), ServerAccessStore)


def test_only_a_push_writes_on_the_server() -> None:
    assert {name for name, writes in OPERATIONS.items() if writes} == {"push"}


def test_every_read_and_a_pull_record_no_write(tmp_path: Path) -> None:
    fake = host()

    fake.check_host_key(ACCESS)
    fake.total_size(ACCESS, ["/srv/app"])
    fake.manifest(ACCESS, "/", ["srv/app"])
    fake.existing(ACCESS, "/", ["srv/app/a.txt"])
    fake.pull(ACCESS, ["/srv/app"], tmp_path)

    assert [c.operation for c in fake.calls] == [
        "check_host_key",
        "total_size",
        "manifest",
        "existing",
        "pull",
    ]
    assert fake.writes_on_server == []


def test_a_push_is_recorded_as_a_write(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("x")
    fake = FakeRemoteHost()

    fake.push(ACCESS, [tmp_path / "x.txt"], "/srv/in")

    assert [c.operation for c in fake.writes_on_server] == ["push"]
    assert fake.content("/srv/in/x.txt") == b"x"


def test_the_standing_follows_the_fake_known_hosts() -> None:
    fake = FakeRemoteHost()
    assert fake.check_host_key(ACCESS).standing is HostKeyStanding.KNOWN

    fake.forget_key()
    assert fake.check_host_key(ACCESS).standing is HostKeyStanding.UNKNOWN

    fake.know_current_key()
    assert fake.check_host_key(ACCESS).standing is HostKeyStanding.KNOWN

    fake.change_key()
    assert fake.check_host_key(ACCESS).standing is HostKeyStanding.CHANGED


def test_trust_makes_an_unknown_key_known() -> None:
    fake = FakeRemoteHost(start_known=False)
    key = fake.check_host_key(ACCESS).host_key
    assert key is not None

    fake.trust(key)

    assert fake.check_host_key(ACCESS).standing is HostKeyStanding.KNOWN


def test_trust_refuses_a_key_that_is_not_the_one_shown_now() -> None:
    fake = FakeRemoteHost(start_known=False)
    key = fake.check_host_key(ACCESS).host_key
    assert key is not None
    fake.change_key()

    with pytest.raises(HostKeyUnknownError):
        fake.trust(key)


def test_trust_never_accepts_a_changed_key() -> None:
    fake = FakeRemoteHost()
    fake.change_key()
    key = fake.check_host_key(ACCESS).host_key
    assert key is not None

    with pytest.raises(HostKeyChangedError):
        fake.trust(key)
    assert fake.check_host_key(ACCESS).standing is HostKeyStanding.CHANGED


def test_a_scripted_connection_failure_is_raised_by_every_operation(
    tmp_path: Path,
) -> None:
    fake = host()
    fake.fail_with = ConnectionFailedError("connection refused")

    for call in (
        lambda: fake.check_host_key(ACCESS),
        lambda: fake.total_size(ACCESS, ["/srv/app"]),
        lambda: fake.manifest(ACCESS, "/", ["srv/app"]),
        lambda: fake.pull(ACCESS, ["/srv/app"], tmp_path),
        lambda: fake.push(ACCESS, [tmp_path], "/srv/in"),
    ):
        with pytest.raises(ConnectionFailedError):
            call()
    assert fake.directories == set()  # the attempts are recorded, nothing is stored


def test_a_pull_places_paths_at_their_full_path_and_skips_special_files(
    tmp_path: Path,
) -> None:
    fake = host()

    skipped = fake.pull(ACCESS, ["/srv/app/sub", "/srv/app/a.txt"], tmp_path)

    assert (tmp_path / "srv/app/sub/b.txt").read_bytes() == b"beta"
    assert (tmp_path / "srv/app/a.txt").read_bytes() == b"alpha"
    assert skipped == []


def test_a_pull_skips_a_device_and_lists_it(tmp_path: Path) -> None:
    skipped = host().pull(ACCESS, ["/srv/app"], tmp_path)

    assert [(s.path, s.reason) for s in skipped] == [("/srv/app/tty", "device")]
    assert not (tmp_path / "srv/app/tty").exists()


def test_a_pull_of_a_missing_path_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    with pytest.raises(RemotePathNotFoundError):
        host().pull(ACCESS, ["/srv/missing"], tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_one_byte_can_be_corrupted_in_transit_and_the_manifests_differ(
    tmp_path: Path,
) -> None:
    fake = host()
    fake.corrupt_in_transit = True
    local = FakeLocalFilesystem()

    fake.pull(ACCESS, ["/srv/app/a.txt", "/srv/app/sub"], tmp_path)

    source = fake.manifest(ACCESS, "/", ["srv/app/a.txt", "srv/app/sub"])
    copy = local.manifest_under(tmp_path, ["srv/app/a.txt", "srv/app/sub"])
    changed = [d.path for d in source.differences_in(copy)]
    assert len(changed) == 1  # exactly one file differs
    assert fake.content("/srv/app/a.txt") == b"alpha"  # the source is untouched


def test_the_manifest_of_the_fake_and_of_its_pull_are_equal(tmp_path: Path) -> None:
    fake = host()
    fake.pull(ACCESS, ["/srv/app"], tmp_path)

    source = fake.manifest(ACCESS, "/", ["srv/app"])
    copy = FakeLocalFilesystem().manifest_under(tmp_path, ["srv/app"])

    assert source.differences_in(copy) == ()


def test_total_size_rounds_each_file_up_to_a_kibibyte() -> None:
    assert host().total_size(ACCESS, ["/srv/app"]) == 2 * 1024


def test_existing_lists_only_what_exists() -> None:
    found = host().existing(ACCESS, "/srv/app", ["a.txt", "nothing.txt", "sub"])

    assert [(e.path, e.size) for e in found] == [
        ("/srv/app/a.txt", 5),
        ("/srv/app/sub", 0),
    ]


def test_the_local_fake_reports_the_configured_room(tmp_path: Path) -> None:
    assert FakeLocalFilesystem(free=10).free_bytes(tmp_path) == 10
    assert FakeLocalFilesystem().free_bytes(tmp_path) > 0


def test_the_access_store_fake_keeps_one_access_per_server() -> None:
    store = FakeServerAccessStore()
    lab = ConfigurationId("lab")
    store.save(ServerAccess(lab, "100", None, 22, "deploy", None))
    store.save(ServerAccess(lab, "100", "web", 2222, "ops", None))

    loaded = store.load(lab, "100")

    assert loaded is not None
    assert (loaded.host, loaded.port, loaded.user) == ("web", 2222, "ops")
    assert store.load(lab, "101") is None


def test_ssh_operations_are_refused_for_an_unknown_host_like_strict_checking(
    tmp_path: Path,
) -> None:
    fake = host()
    fake.forget_key()

    for call in (
        lambda: fake.total_size(ACCESS, ["/srv/app"]),
        lambda: fake.manifest(ACCESS, "/", ["srv/app"]),
        lambda: fake.existing(ACCESS, "/", ["srv/app"]),
        lambda: fake.pull(ACCESS, ["/srv/app"], tmp_path),
        lambda: fake.push(ACCESS, [tmp_path], "/srv/in"),
    ):
        with pytest.raises(HostKeyUnknownError):
            call()
    assert list(tmp_path.iterdir()) == []
    assert "/srv/in" not in fake.directories


def test_ssh_operations_are_refused_for_a_changed_host_key(tmp_path: Path) -> None:
    fake = host()
    fake.change_key()

    with pytest.raises(HostKeyChangedError):
        fake.pull(ACCESS, ["/srv/app"], tmp_path)
    with pytest.raises(HostKeyChangedError):
        fake.push(ACCESS, [tmp_path], "/srv/in")
    assert list(tmp_path.iterdir()) == []


def test_an_unreadable_path_is_permission_denied(tmp_path: Path) -> None:
    fake = host()
    fake.unreadable.add("/srv/app/sub")

    with pytest.raises(PermissionDeniedError):
        fake.pull(ACCESS, ["/srv/app/sub"], tmp_path)
    with pytest.raises(PermissionDeniedError):
        fake.manifest(ACCESS, "/", ["srv/app"])
    with pytest.raises(PermissionDeniedError):
        fake.total_size(ACCESS, ["/srv/app/sub/b.txt"])
