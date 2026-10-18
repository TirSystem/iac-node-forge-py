"""The tar streams of a transfer: what is refused, skipped and kept."""

import io
import os
import socket
import stat
import sys
import tarfile
from pathlib import Path

import pytest

from iac_node_forge.application.errors import TransferFailedError, UnsafeArchiveError
from iac_node_forge.infrastructure.filesystem import LocalDiskFilesystem
from iac_node_forge.infrastructure.openssh._tar_stream import pack, unpack
from tests.unit.application.transfer_fakes import symlinks_supported

posix_only = pytest.mark.skipif(os.name != "posix", reason="needs POSIX modes")
needs_links = pytest.mark.skipif(
    not symlinks_supported(), reason="symbolic links are not allowed here"
)


def archive(*members: tarfile.TarInfo, data: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w|", format=tarfile.GNU_FORMAT) as tar:
        for member in members:
            content = (data or {}).get(member.name)
            if content is not None:
                member.size = len(content)
                tar.addfile(member, io.BytesIO(content))
            else:
                tar.addfile(member)
    return buffer.getvalue()


def file_member(name: str, mode: int = 0o644) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.mode = mode
    return info


def link_member(name: str, target: str, hard: bool = False) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.LNKTYPE if hard else tarfile.SYMTYPE
    info.linkname = target
    return info


def special_member(name: str, kind: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    return info


def run_unpack(content: bytes, destination: Path, names: list[str]) -> list[str]:
    skipped = unpack(io.BytesIO(content), destination, names)
    return [f"{s.path}: {s.reason}" for s in skipped]


def test_members_under_a_requested_path_are_extracted_at_their_full_path(
    tmp_path: Path,
) -> None:
    content = archive(
        file_member("etc/nginx/nginx.conf"), data={"etc/nginx/nginx.conf": b"x"}
    )

    skipped = run_unpack(content, tmp_path, ["etc/nginx"])

    assert skipped == []
    assert (tmp_path / "etc/nginx/nginx.conf").read_bytes() == b"x"


@pytest.mark.parametrize("name", ["/etc/passwd", "../evil", "etc/../../evil"])
def test_an_absolute_or_dotdot_member_name_is_refused(
    tmp_path: Path, name: str
) -> None:
    content = archive(file_member(name), data={name: b"x"})

    with pytest.raises(UnsafeArchiveError):
        run_unpack(content, tmp_path / "out", ["etc"])

    assert not (tmp_path / "evil").exists()


def test_a_member_that_was_not_requested_is_refused(tmp_path: Path) -> None:
    content = archive(
        file_member("var/www/index.html"), data={"var/www/index.html": b""}
    )

    with pytest.raises(UnsafeArchiveError):
        run_unpack(content, tmp_path, ["etc/nginx"])


def test_a_member_whose_name_only_starts_like_a_requested_path_is_refused(
    tmp_path: Path,
) -> None:
    content = archive(file_member("etc/nginx2/x"), data={"etc/nginx2/x": b""})

    with pytest.raises(UnsafeArchiveError):
        run_unpack(content, tmp_path, ["etc/nginx"])


def test_devices_and_pipes_are_skipped_and_listed(tmp_path: Path) -> None:
    content = archive(
        special_member("d/null", tarfile.CHRTYPE),
        special_member("d/disk", tarfile.BLKTYPE),
        special_member("d/pipe", tarfile.FIFOTYPE),
    )

    skipped = run_unpack(content, tmp_path, ["d"])

    assert skipped == ["/d/null: device", "/d/disk: device", "/d/pipe: named pipe"]
    assert not (tmp_path / "d" / "null").exists()


@needs_links
def test_a_relative_link_inside_the_destination_is_kept_as_a_link(
    tmp_path: Path,
) -> None:
    content = archive(file_member("d/a"), link_member("d/l", "a"), data={"d/a": b"1"})

    assert run_unpack(content, tmp_path, ["d"]) == []

    assert os.readlink(tmp_path / "d" / "l") == "a"


@needs_links
@pytest.mark.parametrize("target", ["/etc/passwd", "../../outside", "../.."])
def test_a_link_that_points_outside_is_refused_and_listed(
    tmp_path: Path, target: str
) -> None:
    content = archive(
        link_member("d/l", target), file_member("d/ok"), data={"d/ok": b"1"}
    )

    skipped = run_unpack(content, tmp_path, ["d"])

    assert len(skipped) == 1 and skipped[0].startswith("/d/l: refused")
    assert not (tmp_path / "d" / "l").is_symlink()
    assert (tmp_path / "d" / "ok").read_bytes() == b"1"


@needs_links
def test_a_file_cannot_be_written_through_a_link_that_leaves_the_destination(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = tmp_path / "dest"
    content = archive(
        link_member("d/l", "../../outside"),
        file_member("d/l/pwned"),
        data={"d/l/pwned": b"x"},
    )

    run_unpack(content, destination, ["d"])

    assert not (outside / "pwned").exists()


@posix_only
def test_the_set_user_id_bit_and_group_write_are_dropped(tmp_path: Path) -> None:
    content = archive(file_member("d/s", 0o4777), data={"d/s": b"x"})

    run_unpack(content, tmp_path, ["d"])

    mode = stat.S_IMODE((tmp_path / "d" / "s").stat().st_mode)
    assert not mode & stat.S_ISUID
    assert not mode & (stat.S_IWGRP | stat.S_IWOTH)


@posix_only
def test_files_are_created_with_the_umask_of_the_destination(tmp_path: Path) -> None:
    content = archive(file_member("d/f", 0o666), data={"d/f": b"x"})
    previous = os.umask(0o077)
    try:
        run_unpack(content, tmp_path, ["d"])
    finally:
        os.umask(previous)

    assert stat.S_IMODE((tmp_path / "d" / "f").stat().st_mode) & 0o077 == 0


def test_a_truncated_stream_fails_with_a_transfer_error(tmp_path: Path) -> None:
    content = archive(file_member("d/big"), data={"d/big": b"x" * 5000})

    with pytest.raises(TransferFailedError):
        run_unpack(content[:700], tmp_path, ["d"])


def test_garbage_fails_with_a_transfer_error(tmp_path: Path) -> None:
    with pytest.raises(TransferFailedError):
        run_unpack(b"this is not a tar archive" * 100, tmp_path, ["d"])


def test_an_empty_stream_means_the_command_died_and_fails(tmp_path: Path) -> None:
    with pytest.raises(TransferFailedError):
        run_unpack(b"", tmp_path, ["d"])


def test_a_stream_that_fails_at_once_creates_no_destination(tmp_path: Path) -> None:
    with pytest.raises(TransferFailedError):
        run_unpack(b"", tmp_path / "backup", ["d"])

    assert not (tmp_path / "backup").exists()


def test_an_archive_without_members_extracts_nothing(tmp_path: Path) -> None:
    assert run_unpack(archive(), tmp_path, ["d"]) == []


def packed_names(source: Path, paths: list[Path]) -> tuple[list[str], list[str]]:
    buffer = io.BytesIO()
    skipped = pack(buffer, paths)
    buffer.seek(0)
    with tarfile.open(fileobj=buffer, mode="r|") as tar:
        names = [m.name for m in tar]
    return names, [f"{s.reason}" for s in skipped]


def test_pack_lists_each_path_by_its_final_name_in_a_stable_order(
    tmp_path: Path,
) -> None:
    (tmp_path / "data" / "sub").mkdir(parents=True)
    (tmp_path / "data" / "b.txt").write_text("b")
    (tmp_path / "data" / "sub" / "a.txt").write_text("a")
    (tmp_path / "solo.txt").write_text("s")

    names, skipped = packed_names(tmp_path, [tmp_path / "data", tmp_path / "solo.txt"])

    assert names == ["data", "data/b.txt", "data/sub", "data/sub/a.txt", "solo.txt"]
    assert skipped == []


def test_pack_records_no_owner(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x")
    buffer = io.BytesIO()
    pack(buffer, [tmp_path / "f"])
    buffer.seek(0)

    with tarfile.open(fileobj=buffer, mode="r|") as tar:
        member = next(iter(tar))

    assert (member.uid, member.gid, member.uname, member.gname) == (0, 0, "", "")


@needs_links
def test_pack_writes_a_link_as_a_link_and_does_not_follow_it(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "secret.txt").write_text("outside")
    os.symlink(tmp_path / "secret.txt", tmp_path / "data" / "l")
    buffer = io.BytesIO()

    pack(buffer, [tmp_path / "data"])

    buffer.seek(0)
    with tarfile.open(fileobj=buffer, mode="r|") as tar:
        members = {m.name: m for m in tar}
    assert members["data/l"].issym()
    assert "secret.txt" not in members


if sys.platform != "win32":  # sockets and pipes need a POSIX system

    def test_pack_skips_a_socket_and_a_pipe_and_lists_them(tmp_path: Path) -> None:
        (tmp_path / "data").mkdir()
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(tmp_path / "data" / "s"))
        os.mkfifo(tmp_path / "data" / "p")
        (tmp_path / "data" / "f").write_text("x")
        try:
            names, skipped = packed_names(tmp_path, [tmp_path / "data"])
        finally:
            sock.close()

        assert names == ["data", "data/f"]
        assert sorted(skipped) == ["named pipe", "socket"]


@needs_links
def test_a_packed_tree_unpacks_to_an_equal_manifest(tmp_path: Path) -> None:
    source = tmp_path / "src"
    (source / "data" / "sub").mkdir(parents=True)
    (source / "data" / "a.txt").write_text("alpha")
    (source / "data" / "sub" / "b.bin").write_bytes(bytes(range(256)))
    os.symlink("a.txt", source / "data" / "link")
    buffer = io.BytesIO()
    pack(buffer, [source / "data"])

    unpack(io.BytesIO(buffer.getvalue()), tmp_path / "dest", ["data"])

    local = LocalDiskFilesystem()
    before = local.manifest_of_paths([source / "data"])
    after = local.manifest_under(tmp_path / "dest", ["data"])
    assert before.differences_in(after) == ()
    assert before.file_count == 2 and before.link_count == 1
