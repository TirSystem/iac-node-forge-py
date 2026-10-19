"""The safe parse of the remote find output into a manifest."""

import pytest

from iac_node_forge.application.errors import TransferFailedError
from iac_node_forge.domain.manifest import EntryKind
from iac_node_forge.infrastructure.openssh._manifest_parse import (
    build_manifest,
    parse_hashes,
    parse_links,
    parse_sizes,
)

HASH = "0123456789abcdef" * 4
ROOT = "/srv"


def test_gnu_and_busybox_hash_lines_are_read() -> None:
    output = f"{HASH}  /srv/app/a.txt\n{HASH} */srv/app/b b.txt\n"

    assert parse_hashes(output, ROOT) == {"app/a.txt": HASH, "app/b b.txt": HASH}


def test_a_gnu_escaped_name_is_unescaped() -> None:
    output = f"\\{HASH}  /srv/a\\nb\\\\c\n"

    assert parse_hashes(output, ROOT) == {"a\nb\\c": HASH}


def test_the_root_slash_is_handled() -> None:
    assert parse_hashes(f"{HASH}  /etc/hosts\n", "/") == {"etc/hosts": HASH}


@pytest.mark.parametrize(
    "output",
    [
        "not a hash line\n",
        f"{HASH}\n",
        f"{HASH[:-1]}  /srv/a\n",
        f"{HASH.upper()}  /srv/a\n",
        f"{HASH}  /elsewhere/a\n",
        f"{HASH}  /srv/\n",
    ],
)
def test_a_hash_line_that_is_not_understood_fails_the_whole_manifest(
    output: str,
) -> None:
    with pytest.raises(TransferFailedError):
        parse_hashes(output, ROOT)


def test_sizes_are_read_with_names_that_hold_spaces() -> None:
    assert parse_sizes("12 /srv/a b\n0 /srv/c\n", ROOT) == {"a b": 12, "c": 0}


def test_a_size_line_that_is_not_understood_fails() -> None:
    with pytest.raises(TransferFailedError):
        parse_sizes("x /srv/a\n", ROOT)


def test_links_come_in_name_and_target_pairs() -> None:
    assert parse_links("/srv/l\ntarget\n/srv/m\n/abs/target\n", ROOT) == {
        "l": "target",
        "m": "/abs/target",
    }


def test_an_odd_number_of_link_lines_fails() -> None:
    with pytest.raises(TransferFailedError):
        parse_links("/srv/l\ntarget\n/srv/m\n", ROOT)


def test_empty_output_is_an_empty_listing() -> None:
    assert parse_hashes("", ROOT) == {}
    assert parse_links("", ROOT) == {}


def test_the_listings_join_into_a_manifest() -> None:
    manifest = build_manifest({"a": HASH}, {"a": 4}, {"l": "a"})

    kinds = {e.path: e.kind for e in manifest.entries}
    assert kinds == {"a": EntryKind.FILE, "l": EntryKind.LINK}
    assert manifest.byte_count == 4


def test_a_file_with_a_hash_and_no_size_means_the_tree_changed() -> None:
    with pytest.raises(TransferFailedError):
        build_manifest({"a": HASH}, {}, {})


def test_a_path_that_is_a_file_and_a_link_fails() -> None:
    with pytest.raises(TransferFailedError):
        build_manifest({"a": HASH}, {"a": 1}, {"a": "x"})
