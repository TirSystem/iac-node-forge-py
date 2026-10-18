"""Manifest, remote paths and TransferReport (ADR-0009)."""

import dataclasses
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from iac_node_forge.domain.errors import (
    InvalidManifestError,
    InvalidRemotePathError,
    InvalidTransferReportError,
    NaiveTimestampError,
)
from iac_node_forge.domain.manifest import (
    DifferenceKind,
    EntryKind,
    Manifest,
    ManifestDifference,
    ManifestEntry,
)
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.remote_path import (
    join_remote,
    validate_relative_path,
    validate_remote_path,
)
from iac_node_forge.domain.transfer_report import (
    MAX_NOTE_LENGTH,
    OverwriteEntry,
    SkippedEntry,
    TransferDirection,
    TransferReport,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def file_entry(path: str, checksum: str = HASH_A, size: int = 3) -> ManifestEntry:
    return ManifestEntry(path, EntryKind.FILE, size, checksum, None)


def link_entry(path: str, target: str = "a.txt") -> ManifestEntry:
    return ManifestEntry(path, EntryKind.LINK, 0, None, target)


@pytest.mark.parametrize("path", ["/", "/etc", "/etc/nginx/nginx.conf"])
def test_a_normalised_absolute_path_is_valid(path: str) -> None:
    assert validate_remote_path("p", path) == path


@pytest.mark.parametrize(
    "path", ["etc", "", "/etc/", "/etc//x", "/etc/../x", "/etc/./x", "/a\nb", "/a\x00b"]
)
def test_a_remote_path_that_is_not_absolute_and_normalised_is_refused(
    path: str,
) -> None:
    with pytest.raises(InvalidRemotePathError):
        validate_remote_path("p", path)


@pytest.mark.parametrize("path", ["", "/etc", "a/../b", "a//b", "a/", "a\nb"])
def test_a_relative_path_that_is_not_relative_and_normalised_is_refused(
    path: str,
) -> None:
    with pytest.raises(InvalidRemotePathError):
        validate_relative_path("p", path)


def test_a_relative_path_is_joined_under_the_root() -> None:
    assert join_remote("/", "etc/nginx") == "/etc/nginx"
    assert join_remote("/srv/data", "a/b") == "/srv/data/a/b"


def test_a_manifest_is_ordered_by_path_whatever_the_input_order() -> None:
    manifest = Manifest.of([file_entry("b"), file_entry("a")])

    assert [e.path for e in manifest.entries] == ["a", "b"]
    assert manifest == Manifest.of([file_entry("a"), file_entry("b")])


def test_a_manifest_is_immutable() -> None:
    manifest = Manifest.of([file_entry("a")])

    with pytest.raises(dataclasses.FrozenInstanceError):
        manifest.entries = ()  # type: ignore[misc]
    assert isinstance(manifest.entries, tuple)


def test_a_manifest_lists_a_path_once() -> None:
    with pytest.raises(InvalidManifestError):
        Manifest.of([file_entry("a"), file_entry("a", HASH_B)])


def test_a_manifest_counts_files_links_and_bytes() -> None:
    manifest = Manifest.of([file_entry("a", size=5), file_entry("b", size=7)])
    manifest = Manifest.of([*manifest.entries, link_entry("c")])

    assert (manifest.file_count, manifest.link_count, manifest.byte_count) == (2, 1, 12)


@pytest.mark.parametrize(
    "build",
    [
        lambda: ManifestEntry("a", EntryKind.FILE, 1, None, None),
        lambda: ManifestEntry("a", EntryKind.FILE, 1, "XYZ", None),
        lambda: ManifestEntry("a", EntryKind.FILE, 1, HASH_A, "t"),
        lambda: ManifestEntry("a", EntryKind.FILE, -1, HASH_A, None),
        lambda: ManifestEntry("a", EntryKind.LINK, 0, None, None),
        lambda: ManifestEntry("a", EntryKind.LINK, 0, HASH_A, "t"),
        lambda: ManifestEntry("a", EntryKind.LINK, 4, None, "t"),
    ],
)
def test_an_entry_that_contradicts_its_kind_is_refused(
    build: Callable[[], object],
) -> None:
    with pytest.raises(InvalidManifestError):
        build()


def test_an_entry_path_must_be_relative() -> None:
    with pytest.raises(InvalidRemotePathError):
        file_entry("/etc/passwd")


def test_equal_manifests_have_no_differences() -> None:
    manifest = Manifest.of([file_entry("a"), link_entry("l")])

    assert manifest.differences_in(Manifest.of(list(manifest.entries))) == ()


def test_a_changed_byte_shows_as_changed() -> None:
    source = Manifest.of([file_entry("a"), file_entry("b")])
    copy = Manifest.of([file_entry("a"), file_entry("b", HASH_B)])

    assert source.differences_in(copy) == (
        ManifestDifference("b", DifferenceKind.CHANGED),
    )


def test_a_missing_and_an_extra_file_are_listed() -> None:
    source = Manifest.of([file_entry("a"), file_entry("b")])
    copy = Manifest.of([file_entry("b"), file_entry("c")])

    assert source.differences_in(copy) == (
        ManifestDifference("a", DifferenceKind.MISSING),
        ManifestDifference("c", DifferenceKind.EXTRA),
    )


def test_a_changed_link_target_or_kind_shows_as_changed() -> None:
    source = Manifest.of([link_entry("l", "a"), file_entry("f")])
    copy = Manifest.of([link_entry("l", "b"), link_entry("f")])

    kinds = {d.path: d.kind for d in source.differences_in(copy)}

    assert kinds == {"l": DifferenceKind.CHANGED, "f": DifferenceKind.CHANGED}


def test_without_drops_the_entries_at_or_under_the_paths() -> None:
    manifest = Manifest.of([file_entry("a/x"), file_entry("a/y"), file_entry("ab")])

    assert [e.path for e in manifest.without(["a"]).entries] == ["ab"]


def report(**overrides: object) -> TransferReport:
    values: dict[str, object] = {
        "direction": TransferDirection.BACKUP,
        "configuration_id": ConfigurationId("lab"),
        "server_identifier": "100",
        "started_at": NOW,
        "finished_at": NOW + timedelta(seconds=3),
        "file_count": 2,
        "link_count": 1,
        "byte_count": 10,
        "skipped": (SkippedEntry("/dev/x", "device"),),
        "overwritten": (OverwriteEntry("/srv/a", 3, NOW),),
        "differences": (),
        "verified": True,
    }
    values.update(overrides)
    return TransferReport(**values)  # type: ignore[arg-type]


def test_a_report_is_immutable_and_holds_only_counts_paths_and_text() -> None:
    result = report()

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.verified = False  # type: ignore[misc]
    names = {f.name for f in dataclasses.fields(result)}
    assert not {n for n in names if "password" in n or "key" in n or "token" in n}


def test_a_report_with_a_naive_time_is_refused() -> None:
    with pytest.raises(NaiveTimestampError):
        report(started_at=datetime(2026, 1, 1))


def test_a_report_that_ends_before_it_starts_is_refused() -> None:
    with pytest.raises(InvalidTransferReportError):
        report(finished_at=NOW - timedelta(seconds=1))


def test_a_report_with_a_negative_count_is_refused() -> None:
    with pytest.raises(InvalidTransferReportError):
        report(byte_count=-1)


def test_a_verified_report_has_no_differences() -> None:
    with pytest.raises(InvalidTransferReportError):
        report(differences=(ManifestDifference("a", DifferenceKind.CHANGED),))


def test_a_report_note_is_bounded() -> None:
    with pytest.raises(InvalidTransferReportError):
        report(note="x" * (MAX_NOTE_LENGTH + 1))


def test_an_overwrite_entry_needs_a_timezone() -> None:
    with pytest.raises(NaiveTimestampError):
        OverwriteEntry("/srv/a", 1, datetime(2026, 1, 1))
