"""LocalDiskFilesystem: room, manifests, destination checks and the report file."""

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from iac_node_forge.application.ports import LocalFilesystemPort
from iac_node_forge.domain.manifest import (
    DifferenceKind,
    EntryKind,
    ManifestDifference,
)
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.transfer_report import (
    OverwriteEntry,
    SkippedEntry,
    TransferDirection,
    TransferReport,
)
from iac_node_forge.infrastructure.filesystem import LocalDiskFilesystem
from tests.unit.application.transfer_fakes import symlinks_supported

needs_links = pytest.mark.skipif(
    not symlinks_supported(), reason="symbolic links are not allowed here"
)
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def report(**overrides: object) -> TransferReport:
    values: dict[str, object] = {
        "direction": TransferDirection.RESTORE,
        "configuration_id": ConfigurationId("lab"),
        "server_identifier": "100",
        "started_at": NOW,
        "finished_at": NOW + timedelta(seconds=2),
        "file_count": 3,
        "link_count": 1,
        "byte_count": 42,
        "skipped": (SkippedEntry("/dev/x", "device"),),
        "overwritten": (OverwriteEntry("/srv/a", 7, NOW),),
        "differences": (),
        "verified": True,
        "note": None,
    }
    values.update(overrides)
    return TransferReport(**values)  # type: ignore[arg-type]


def test_the_adapter_satisfies_its_port() -> None:
    assert isinstance(LocalDiskFilesystem(), LocalFilesystemPort)


def test_free_bytes_is_the_disk_usage_of_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []

    def usage(path: Path) -> shutil._ntuple_diskusage:
        seen.append(Path(path))
        return shutil._ntuple_diskusage(100, 40, 60)

    monkeypatch.setattr(shutil, "disk_usage", usage)

    assert LocalDiskFilesystem().free_bytes(tmp_path) == 60
    assert seen == [tmp_path]


def test_free_bytes_of_a_directory_to_be_created_uses_the_nearest_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []

    def usage(path: Path) -> shutil._ntuple_diskusage:
        seen.append(Path(path))
        return shutil._ntuple_diskusage(100, 40, 60)

    monkeypatch.setattr(shutil, "disk_usage", usage)

    LocalDiskFilesystem().free_bytes(tmp_path / "new" / "deeper")

    assert seen == [tmp_path]


def test_free_bytes_of_a_real_directory_is_positive(tmp_path: Path) -> None:
    assert LocalDiskFilesystem().free_bytes(tmp_path / "later") > 0


def test_a_directory_that_does_not_exist_or_is_empty_is_new_or_empty(
    tmp_path: Path,
) -> None:
    local = LocalDiskFilesystem()
    (tmp_path / "empty").mkdir()
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("x")
    (tmp_path / "file").write_text("x")

    assert local.is_new_or_empty(tmp_path / "missing")
    assert local.is_new_or_empty(tmp_path / "empty")
    assert not local.is_new_or_empty(tmp_path / "full")
    assert not local.is_new_or_empty(tmp_path / "file")


def test_missing_lists_the_paths_that_do_not_exist(tmp_path: Path) -> None:
    (tmp_path / "here").write_text("x")

    result = LocalDiskFilesystem().missing([tmp_path / "here", tmp_path / "gone"])

    assert result == [tmp_path / "gone"]


def test_size_adds_the_regular_files_in_and_at_the_paths(tmp_path: Path) -> None:
    (tmp_path / "d" / "s").mkdir(parents=True)
    (tmp_path / "d" / "a").write_bytes(b"12345")
    (tmp_path / "d" / "s" / "b").write_bytes(b"123")
    (tmp_path / "c").write_bytes(b"12")

    assert LocalDiskFilesystem().size([tmp_path / "d", tmp_path / "c"]) == 10


def test_a_manifest_lists_files_by_final_name_with_size_and_sha256(
    tmp_path: Path,
) -> None:
    (tmp_path / "data" / "sub").mkdir(parents=True)
    (tmp_path / "data" / "sub" / "a.bin").write_bytes(b"alpha")
    (tmp_path / "solo.txt").write_bytes(b"solo")

    manifest = LocalDiskFilesystem().manifest_of_paths(
        [tmp_path / "data", tmp_path / "solo.txt"]
    )

    assert [(e.path, e.size) for e in manifest.entries] == [
        ("data/sub/a.bin", 5),
        ("solo.txt", 4),
    ]
    assert manifest.entries[0].checksum == hashlib.sha256(b"alpha").hexdigest()
    assert manifest.entries[0].kind is EntryKind.FILE


def test_a_manifest_under_a_directory_is_relative_to_it(tmp_path: Path) -> None:
    (tmp_path / "etc" / "nginx").mkdir(parents=True)
    (tmp_path / "etc" / "nginx" / "n.conf").write_bytes(b"c")
    (tmp_path / "etc" / "other").write_bytes(b"o")

    manifest = LocalDiskFilesystem().manifest_under(tmp_path, ["etc/nginx"])

    assert [e.path for e in manifest.entries] == ["etc/nginx/n.conf"]


def test_a_manifest_under_lists_a_single_file_by_its_relative_path(
    tmp_path: Path,
) -> None:
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "hosts").write_bytes(b"h")

    manifest = LocalDiskFilesystem().manifest_under(tmp_path, ["etc/hosts"])

    assert [e.path for e in manifest.entries] == ["etc/hosts"]


def test_a_path_that_does_not_exist_lists_nothing_so_the_comparison_says_missing(
    tmp_path: Path,
) -> None:
    local = LocalDiskFilesystem()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a").write_bytes(b"a")
    source = local.manifest_of_paths([tmp_path / "src"])

    copy = local.manifest_under(tmp_path / "empty", ["src"])

    assert source.differences_in(copy) == (
        ManifestDifference("src/a", DifferenceKind.MISSING),
    )


def test_one_changed_byte_is_a_difference(tmp_path: Path) -> None:
    local = LocalDiskFilesystem()
    (tmp_path / "a" / "d").mkdir(parents=True)
    (tmp_path / "b" / "d").mkdir(parents=True)
    (tmp_path / "a" / "d" / "f").write_bytes(b"abcdef")
    (tmp_path / "b" / "d" / "f").write_bytes(b"abcdeg")

    one = local.manifest_under(tmp_path / "a", ["d"])
    two = local.manifest_under(tmp_path / "b", ["d"])

    assert one.differences_in(two) == (
        ManifestDifference("d/f", DifferenceKind.CHANGED),
    )


@needs_links
def test_a_link_is_listed_with_its_target_and_not_followed(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "outside.txt").write_bytes(b"outside")
    os.symlink("a.txt", tmp_path / "d" / "rel")
    os.symlink(tmp_path / "outside.txt", tmp_path / "d" / "abs")
    os.symlink("nowhere", tmp_path / "d" / "dangling")

    manifest = LocalDiskFilesystem().manifest_of_paths([tmp_path / "d"])

    kinds = {e.path: (e.kind, e.link_target) for e in manifest.entries}
    assert kinds["d/rel"] == (EntryKind.LINK, "a.txt")
    assert kinds["d/dangling"] == (EntryKind.LINK, "nowhere")
    assert kinds["d/abs"][0] is EntryKind.LINK
    assert manifest.file_count == 0  # outside.txt is not under d


def test_the_report_is_saved_as_json_without_secrets(tmp_path: Path) -> None:
    directory = tmp_path / "reports" / "restore"

    path = Path(LocalDiskFilesystem().save_report(directory, report()))

    assert path.parent == directory and path.suffix == ".json"
    content = json.loads(path.read_text("utf-8"))
    assert content["direction"] == "restore"
    assert content["verified"] is True
    assert content["skipped"] == [{"path": "/dev/x", "reason": "device"}]
    assert content["overwritten"][0]["path"] == "/srv/a"
    assert content["report_file"] == str(path)
    assert not {k for k in content if "pass" in k or "key" in k or "token" in k}


def test_a_second_report_does_not_replace_the_first(tmp_path: Path) -> None:
    local = LocalDiskFilesystem()

    first = local.save_report(tmp_path, report())
    second = local.save_report(tmp_path, report())

    assert first != second
    assert len(list(tmp_path.iterdir())) == 2


def test_the_report_file_uses_unix_line_endings(tmp_path: Path) -> None:
    path = Path(LocalDiskFilesystem().save_report(tmp_path, report()))

    assert b"\r" not in path.read_bytes()
