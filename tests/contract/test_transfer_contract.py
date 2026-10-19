"""One set of behaviours of the transfer ports, in the fake and on a real server.

The ``world`` fixture runs every test against the in-process fake and, opt-in
(``IACNF_SSHD_TEST=1``, marker ``integration``), against the OpenSSH adapters and
a throw-away sshd container (ADR-0001, ADR-0009).
"""

import os
import random
from pathlib import Path

import pytest

from iac_node_forge.application.errors import (
    ConnectionFailedError,
    HostKeyChangedError,
    HostKeyUnknownError,
    PermissionDeniedError,
    RemotePathNotFoundError,
)
from iac_node_forge.domain.host_key import HostKeyStanding
from iac_node_forge.domain.manifest import (
    DifferenceKind,
    EntryKind,
    Manifest,
    ManifestDifference,
)
from tests.contract.transfer_under_test import (
    Entry,
    Link,
    NamedPipe,
    TransferWorld,
)
from tests.unit.application.transfer_fakes import (
    FakeLocalFilesystem,
    symlinks_supported,
)

BIG = random.Random(7).randbytes(3 * 1024 * 1024)
TREE: dict[str, Entry] = {
    "data/readme.txt": b"hello\n",
    "data/empty.txt": b"",
    "data/bin/blob.bin": bytes(range(256)) * 40,
    "data/bin/big.bin": BIG,
    "data/with space/ünï.txt": b"unicode name",
    "data/sub/deep/a.txt": b"a",
    "other/not-requested.txt": b"left alone",
}
needs_links = pytest.mark.skipif(
    not symlinks_supported(), reason="symbolic links are not allowed here"
)


def remote_manifest(world: TransferWorld, *relative: str) -> Manifest:
    return world.reader.manifest(
        world.access, "/", [world.under_root(path) for path in relative]
    )


def pulled(world: TransferWorld, destination: Path, *relative: str) -> Manifest:
    return world.local.manifest_under(
        destination, [world.under_root(path) for path in relative]
    )


def pull(world: TransferWorld, tmp_path: Path, *relative: str) -> Path:
    destination = tmp_path / "backup"
    world.transfer.pull(
        world.access, [world.remote(path) for path in relative], destination
    )
    return destination


def local_tree(base: Path) -> Path:
    """Make the tree a restore sends; return its top directory."""
    top = base / "tree"
    (top / "sub" / "deep").mkdir(parents=True)
    (top / "a.txt").write_bytes(b"alpha")
    (top / "sub" / "b.bin").write_bytes(bytes(range(256)) * 10)
    (top / "sub" / "deep" / "empty").write_bytes(b"")
    (base / "solo.txt").write_bytes(b"solo")
    return top


# Copying a tree to the PC


def test_a_tree_copied_to_the_pc_matches_the_server_by_manifest(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed(TREE)

    destination = pull(world, tmp_path, "data")

    source = remote_manifest(world, "data")
    assert source.file_count == 6
    assert source.differences_in(pulled(world, destination, "data")) == ()
    copied = destination / world.under_root("data/readme.txt")
    assert copied.read_bytes() == b"hello\n"


def test_a_path_is_placed_under_the_destination_at_its_full_path(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed(TREE)

    destination = pull(world, tmp_path, "data/sub")

    assert (destination / world.under_root("data/sub/deep/a.txt")).read_bytes() == b"a"
    assert not (destination / world.under_root("other")).exists()
    assert not (destination / world.under_root("data/readme.txt")).exists()


def test_a_single_file_and_a_directory_can_be_copied_together(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed(TREE)

    destination = pull(world, tmp_path, "data/readme.txt", "data/sub")

    source = remote_manifest(world, "data/readme.txt", "data/sub")
    assert source.file_count == 2
    copy = pulled(world, destination, "data/readme.txt", "data/sub")
    assert source.differences_in(copy) == ()


# Copying a tree to the server


def test_a_tree_sent_to_the_server_matches_the_pc_by_manifest(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed({})
    top = local_tree(tmp_path)
    paths = [top, tmp_path / "solo.txt"]

    skipped = world.transfer.push(world.access, paths, world.remote("restored/in/here"))

    assert skipped == []
    source = world.local.manifest_of_paths(paths)
    copy = world.reader.manifest(
        world.access, world.remote("restored/in/here"), ["tree", "solo.txt"]
    )
    assert source.file_count == 4
    assert source.differences_in(copy) == ()


def test_a_round_trip_keeps_every_byte(world: TransferWorld, tmp_path: Path) -> None:
    world.seed(TREE)
    destination = pull(world, tmp_path, "data")
    back = destination / world.under_root("data")

    world.transfer.push(world.access, [back], world.remote("copy"))

    original = world.reader.manifest(world.access, world.remote_root, ["data"])
    copy = world.reader.manifest(world.access, world.remote("copy"), ["data"])
    assert original.differences_in(copy) == ()
    assert original.byte_count == copy.byte_count > len(BIG)


def test_a_restore_replaces_what_it_names_and_deletes_nothing(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed(TREE)
    top = tmp_path / "data"
    top.mkdir()
    (top / "readme.txt").write_bytes(b"replaced\n")
    before = world.reader.existing(
        world.access, world.remote_root, ["data/readme.txt", "data/nothing.txt"]
    )

    world.transfer.push(world.access, [top], world.remote_root)

    assert [(e.path, e.size) for e in before] == [(world.remote("data/readme.txt"), 6)]
    assert before[0].modified_at.tzinfo is not None
    after = world.reader.manifest(world.access, world.remote_root, ["data"])
    paths = {e.path: e for e in after.entries}
    assert paths["data/readme.txt"].size == 9
    assert "data/empty.txt" in paths and "data/sub/deep/a.txt" in paths


def test_existing_lists_only_what_exists(world: TransferWorld) -> None:
    world.seed(TREE)

    found = world.reader.existing(
        world.access, world.remote_root, ["data/nothing", "other"]
    )

    assert [e.path for e in found] == [world.remote("other")]


# Verification


def test_a_changed_byte_is_reported_by_the_manifest_comparison(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed(TREE)
    destination = pull(world, tmp_path, "data")
    victim = destination / world.under_root("data/readme.txt")
    victim.write_bytes(b"hellp\n")  # one byte, same size

    copy = pulled(world, destination, "data")

    path = world.under_root("data/readme.txt")
    assert remote_manifest(world, "data").differences_in(copy) == (
        ManifestDifference(path, DifferenceKind.CHANGED),
    )


def test_a_missing_and_an_extra_file_are_reported(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed(TREE)
    destination = pull(world, tmp_path, "data")
    (destination / world.under_root("data/empty.txt")).unlink()
    (destination / world.under_root("data/extra.txt")).write_bytes(b"x")

    copy = pulled(world, destination, "data")

    kinds = {
        d.path: d.kind for d in remote_manifest(world, "data").differences_in(copy)
    }
    assert kinds == {
        world.under_root("data/empty.txt"): DifferenceKind.MISSING,
        world.under_root("data/extra.txt"): DifferenceKind.EXTRA,
    }


def test_the_manifest_has_the_size_and_sha256_of_every_file(
    world: TransferWorld,
) -> None:
    world.seed(TREE)

    manifest = remote_manifest(world, "data")

    entry = next(e for e in manifest.entries if e.path.endswith("data/readme.txt"))
    assert entry.size == 6 and entry.kind is EntryKind.FILE
    assert entry.checksum == (
        "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
    )


# Room


def test_the_size_to_copy_covers_the_bytes_and_the_room_is_known(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed(TREE)

    needed = world.reader.total_size(world.access, [world.remote("data")])

    data = [c for p, c in TREE.items() if p.startswith("data/")]
    assert needed >= sum(len(c) for c in data if isinstance(c, bytes))
    assert world.local.free_bytes(tmp_path / "not-yet") > 0


def test_a_destination_without_room_is_seen_before_anything_is_copied(
    world: TransferWorld, tmp_path: Path
) -> None:
    if not isinstance(world.local, FakeLocalFilesystem):
        pytest.skip("room can only be set in the fake; see the unit test of the disk")
    world.seed(TREE)
    world.local.free = 1024

    needed = world.reader.total_size(world.access, [world.remote("data")])

    assert needed > world.local.free_bytes(tmp_path)
    assert world.server_writes() == 0


# A path that does not exist


def test_a_path_that_does_not_exist_is_reported_by_every_read(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed(TREE)
    missing = world.remote("no/such/place")

    with pytest.raises(RemotePathNotFoundError):
        world.reader.total_size(world.access, [missing])
    with pytest.raises(RemotePathNotFoundError):
        world.reader.manifest(world.access, "/", [world.under_root("no/such/place")])
    with pytest.raises(RemotePathNotFoundError):
        world.transfer.pull(world.access, [missing], tmp_path / "backup")


# Links, pipes


@needs_links
def test_links_are_copied_as_links_and_never_followed(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed(
        {
            "links/target.txt": b"target",
            "links/relative": Link("target.txt"),
            "links/dangling": Link("nowhere"),
            "outside.txt": b"outside the requested tree",
        }
    )

    destination = pull(world, tmp_path, "links")

    base = destination / world.under_root("links")
    assert os.readlink(base / "relative") == "target.txt"
    assert os.readlink(base / "dangling") == "nowhere"
    source = remote_manifest(world, "links")
    assert source.link_count == 2
    assert source.differences_in(pulled(world, destination, "links")) == ()
    assert not (destination / world.under_root("outside.txt")).exists()


@needs_links
def test_a_link_that_points_out_of_the_destination_is_skipped_and_listed(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed({"links/ok.txt": b"ok", "links/absolute": Link("/etc/passwd")})

    destination = tmp_path / "backup"
    skipped = world.transfer.pull(world.access, [world.remote("links")], destination)

    assert [s.path for s in skipped] == [world.remote("links/absolute")]
    assert skipped[0].reason.startswith("refused")
    assert not (destination / world.under_root("links/absolute")).is_symlink()
    assert (destination / world.under_root("links/ok.txt")).read_bytes() == b"ok"


@needs_links
def test_a_restore_sends_a_link_as_a_link(world: TransferWorld, tmp_path: Path) -> None:
    world.seed({})
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"must not travel")
    top = tmp_path / "links"
    top.mkdir()
    (top / "file.txt").write_bytes(b"f")
    os.symlink("file.txt", top / "relative")
    os.symlink(outside, top / "absolute")

    skipped = world.transfer.push(world.access, [top], world.remote("restored"))

    assert skipped == []
    copy = world.reader.manifest(world.access, world.remote("restored"), ["links"])
    links = {e.path: e.link_target for e in copy.entries if e.kind is EntryKind.LINK}
    assert links["links/relative"] == "file.txt"
    assert links["links/absolute"] == os.readlink(top / "absolute")
    assert "outside.txt" not in {e.path for e in copy.entries}
    assert copy.file_count == 1


def test_a_named_pipe_is_skipped_and_listed(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed({"pipes/file.txt": b"f", "pipes/fifo": NamedPipe()})

    destination = tmp_path / "backup"
    skipped = world.transfer.pull(world.access, [world.remote("pipes")], destination)

    assert [s.path for s in skipped] == [world.remote("pipes/fifo")]
    assert (destination / world.under_root("pipes/file.txt")).read_bytes() == b"f"
    assert not (destination / world.under_root("pipes/fifo")).exists()


# The host key


def test_an_unknown_host_key_is_shown_then_trusted_then_known(
    world: TransferWorld,
) -> None:
    world.seed(TREE)
    world.arrange_host_key(HostKeyStanding.UNKNOWN)

    check = world.reader.check_host_key(world.access)

    assert check.standing is HostKeyStanding.UNKNOWN
    assert check.host_key is not None
    assert check.host_key.fingerprint.startswith("SHA256:")
    with pytest.raises(HostKeyUnknownError):
        remote_manifest(world, "data")

    world.known_hosts.trust(check.host_key)

    known = world.reader.check_host_key(world.access)
    assert known.standing is HostKeyStanding.KNOWN
    assert known.host_key == check.host_key
    assert remote_manifest(world, "data").file_count == 6


def test_a_known_host_key_is_known_and_stable(world: TransferWorld) -> None:
    world.arrange_host_key(HostKeyStanding.KNOWN)

    first = world.reader.check_host_key(world.access)
    second = world.reader.check_host_key(world.access)

    assert first.standing is HostKeyStanding.KNOWN
    assert first.host_key == second.host_key


def test_a_changed_host_key_stops_everything_and_is_never_accepted(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed(TREE)
    world.arrange_host_key(HostKeyStanding.CHANGED)

    check = world.reader.check_host_key(world.access)

    assert check.standing is HostKeyStanding.CHANGED
    assert check.host_key is not None
    with pytest.raises(HostKeyChangedError):
        remote_manifest(world, "data")
    with pytest.raises(HostKeyChangedError):
        world.transfer.pull(world.access, [world.remote("data")], tmp_path / "backup")
    with pytest.raises(HostKeyChangedError):
        world.transfer.push(world.access, [tmp_path], world.remote("never"))
    with pytest.raises(HostKeyChangedError):
        world.known_hosts.trust(check.host_key)
    assert world.reader.check_host_key(world.access).standing is (
        HostKeyStanding.CHANGED
    )
    assert not (tmp_path / "backup").exists()


def test_a_fingerprint_that_is_not_the_one_shown_is_not_trusted(
    world: TransferWorld,
) -> None:
    world.arrange_host_key(HostKeyStanding.UNKNOWN)
    check = world.reader.check_host_key(world.access)
    assert check.host_key is not None
    forged = type(check.host_key)(
        check.host_key.host,
        check.host_key.port,
        check.host_key.key_type,
        "SHA256:" + "A" * 43,
    )

    with pytest.raises(HostKeyUnknownError):
        world.known_hosts.trust(forged)

    assert world.reader.check_host_key(world.access).standing is (
        HostKeyStanding.UNKNOWN
    )


# A backup reads only


def test_a_backup_changes_nothing_on_the_server(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed(TREE)
    root = world.remote_root
    before = world.reader.manifest(world.access, root, ["data", "other"])

    world.reader.check_host_key(world.access)
    world.reader.total_size(world.access, [world.remote("data")])
    world.reader.existing(world.access, root, ["data/readme.txt"])
    remote_manifest(world, "data")
    pull(world, tmp_path, "data")

    after = world.reader.manifest(world.access, root, ["data", "other"])
    assert before == after
    assert world.server_writes() in (0, None)


def test_only_a_restore_writes_on_the_server(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed({})
    top = local_tree(tmp_path)

    world.transfer.push(world.access, [top], world.remote("restored"))

    assert world.server_writes() in (1, None)


# Failures


def test_an_unreachable_server_is_a_failed_connection(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed(TREE)
    access = world.unreachable_access()

    with pytest.raises(ConnectionFailedError):
        world.reader.check_host_key(access)
    with pytest.raises(ConnectionFailedError):
        world.reader.total_size(access, [world.remote("data")])
    with pytest.raises(ConnectionFailedError):
        world.reader.manifest(access, "/", [world.under_root("data")])
    with pytest.raises(ConnectionFailedError):
        world.transfer.pull(access, [world.remote("data")], tmp_path / "backup")
    with pytest.raises(ConnectionFailedError):
        world.transfer.push(access, [tmp_path], world.remote("never"))


def test_a_path_the_user_may_not_read_is_permission_denied(
    world: TransferWorld, tmp_path: Path
) -> None:
    world.seed({"locked/f.txt": b"secret data", "open/f.txt": b"fine"})
    world.deny_reading("locked")

    with pytest.raises(PermissionDeniedError):
        world.reader.total_size(world.access, [world.remote("locked")])
    with pytest.raises(PermissionDeniedError):
        remote_manifest(world, "locked")
    with pytest.raises(PermissionDeniedError):
        world.transfer.pull(world.access, [world.remote("locked")], tmp_path / "b")
