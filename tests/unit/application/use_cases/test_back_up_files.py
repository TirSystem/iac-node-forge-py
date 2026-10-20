"""UC-016: preview and make a backup with fakes."""

from pathlib import Path

from iac_node_forge.application.errors import TransferFailedError
from iac_node_forge.application.transfer_dto import TransferOutcome
from iac_node_forge.application.use_cases.back_up_files import (
    MARGIN_BYTES,
    required_room,
)
from iac_node_forge.domain.manifest import DifferenceKind
from iac_node_forge.domain.transfer_report import TransferDirection
from tests.unit.application.transfer_fakes import FakeRemoteHost
from tests.unit.application.use_cases.transfer_setup import build


def operations(host: FakeRemoteHost) -> list[str]:
    return [call.operation for call in host.calls]


def test_the_required_room_is_the_data_plus_ten_percent_and_16_mib() -> None:
    assert required_room(0) == MARGIN_BYTES == 16 * 1024 * 1024
    assert required_room(1000) == 1100 + MARGIN_BYTES


def test_preview_states_the_connection_the_size_and_the_room(tmp_path: Path) -> None:
    setup = build()

    preview = setup.backup.preview_backup(
        "Lab", "100", ["/srv/app"], str(tmp_path / "out"), None
    )

    assert preview.outcome is TransferOutcome.READY
    assert (preview.host, preview.port, preview.user) == ("192.0.2.10", 22, "deploy")
    assert preview.host_key is not None
    assert preview.total_bytes == 2048
    assert preview.required_bytes == required_room(2048)
    assert preview.free_bytes is not None
    assert preview.managed is True
    assert not (tmp_path / "out").exists()
    assert setup.host.writes_on_server == []


def test_a_backup_copies_verifies_and_saves_the_report(tmp_path: Path) -> None:
    setup = build()
    destination = tmp_path / "out"

    result = setup.backup.request_backup("Lab", "100", ["/srv/app"], str(destination))

    assert result.outcome is TransferOutcome.COMPLETED
    assert (destination / "srv/app/a.txt").read_bytes() == b"alpha"
    assert (destination / "srv/app/sub/b.txt").read_bytes() == b"beta"
    report = result.report
    assert report is not None
    assert report.verified and report.differences == ()
    assert (report.direction, report.file_count, report.byte_count) == (
        TransferDirection.BACKUP,
        2,
        9,
    )
    assert report.report_file is not None
    assert Path(report.report_file).parent == destination
    assert setup.host.writes_on_server == []
    assert operations(setup.host).count("pull") == 1


def test_one_changed_byte_is_a_verification_failure_never_a_success(
    tmp_path: Path,
) -> None:
    setup = build(host=FakeRemoteHost(corrupt_in_transit=True))
    setup.host.add_file("/srv/app/a.txt", b"alpha")

    result = setup.backup.request_backup(
        "Lab", "100", ["/srv/app"], str(tmp_path / "o")
    )

    assert result.outcome is TransferOutcome.VERIFICATION_FAILED
    assert result.report is not None
    assert result.report.verified is False
    assert [(d.path, d.kind) for d in result.report.differences] == [
        ("srv/app/a.txt", DifferenceKind.CHANGED)
    ]
    assert "srv/app/a.txt" in result.reason


def test_a_destination_without_room_is_refused_before_anything_is_copied(
    tmp_path: Path,
) -> None:
    setup = build(free=1000)

    result = setup.backup.request_backup(
        "Lab", "100", ["/srv/app"], str(tmp_path / "o")
    )

    assert result.outcome is TransferOutcome.NO_ROOM
    assert "1000 bytes free" in result.reason
    assert "pull" not in operations(setup.host)
    assert not (tmp_path / "o").exists()


def test_a_missing_remote_path_is_named(tmp_path: Path) -> None:
    result = build().backup.preview_backup(
        "Lab", "100", ["/srv/app", "/srv/gone"], str(tmp_path / "o"), None
    )

    assert result.outcome is TransferOutcome.PATH_NOT_FOUND
    assert "/srv/gone" in result.reason


def test_a_destination_that_is_not_empty_is_refused(tmp_path: Path) -> None:
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "x").write_text("x", encoding="utf-8")
    setup = build()

    result = setup.backup.request_backup(
        "Lab", "100", ["/srv/app"], str(tmp_path / "taken")
    )

    assert result.outcome is TransferOutcome.DESTINATION_NOT_EMPTY
    assert setup.host.calls == []


def test_an_empty_existing_destination_is_accepted(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()

    preview = build().backup.preview_backup(
        "Lab", "100", ["/srv/app"], str(tmp_path / "empty"), None
    )

    assert preview.outcome is TransferOutcome.READY


def test_paths_that_are_not_valid_are_refused_without_a_connection(
    tmp_path: Path,
) -> None:
    setup = build()
    destination = str(tmp_path / "o")

    refused = [
        setup.backup.preview_backup("Lab", "100", paths, destination, None)
        for paths in ([], ["relative"], ["/a/../b"], ["/"], ["/srv", "/srv/app"])
    ]

    assert {r.outcome for r in refused} == {TransferOutcome.INVALID_PATH}
    assert setup.host.calls == []


def test_a_link_the_pull_refused_is_listed_and_left_out_of_the_comparison(
    tmp_path: Path,
) -> None:
    setup = build()
    setup.host.add_link("/srv/app/abs", "/etc/passwd")
    setup.host.add_special("/srv/app/tty", "device")

    result = setup.backup.request_backup(
        "Lab", "100", ["/srv/app"], str(tmp_path / "o")
    )

    assert result.outcome is TransferOutcome.COMPLETED
    assert result.report is not None
    reasons = {s.path: s.reason for s in result.report.skipped}
    assert reasons["/srv/app/abs"].startswith("refused:")
    assert reasons["/srv/app/tty"] == "device"
    assert result.report.differences == ()


def test_an_unknown_host_key_stops_a_request_and_the_preview_can_accept_it(
    tmp_path: Path,
) -> None:
    setup = build(host=FakeRemoteHost(start_known=False))
    setup.host.add_file("/srv/app/a.txt", b"alpha")
    destination = str(tmp_path / "o")
    key = setup.host.key.fingerprint

    stopped = setup.backup.request_backup("Lab", "100", ["/srv/app"], destination)
    accepted = setup.backup.preview_backup("Lab", "100", ["/srv/app"], destination, key)
    done = setup.backup.request_backup("Lab", "100", ["/srv/app"], destination)

    assert stopped.outcome is TransferOutcome.HOST_KEY_UNKNOWN
    assert stopped.host_key is not None
    assert accepted.outcome is TransferOutcome.READY
    assert done.outcome is TransferOutcome.COMPLETED


class _FailingPull(FakeRemoteHost):
    def pull(self, *arguments: object) -> list[object]:  # type: ignore[override]
        super().pull(*arguments)  # type: ignore[arg-type]
        raise TransferFailedError("the connection broke")


def test_a_copy_that_stops_is_reported_as_incomplete(tmp_path: Path) -> None:
    host = _FailingPull()
    host.add_file("/srv/app/a.txt", b"alpha")
    setup = build(host=host)

    result = setup.backup.request_backup(
        "Lab", "100", ["/srv/app"], str(tmp_path / "o")
    )

    assert result.outcome is TransferOutcome.TRANSFER_FAILED
    assert result.report is not None
    assert result.report.verified is False
    assert result.report.note is not None
    assert result.report.note.startswith("incomplete:")
    assert result.report.report_file is not None
