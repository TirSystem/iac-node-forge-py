"""UC-017: preview and make a restore with fakes."""

from pathlib import Path

import pytest

from iac_node_forge.application.dto import Confirmation
from iac_node_forge.application.errors import TransferFailedError
from iac_node_forge.application.transfer_dto import TransferOutcome
from iac_node_forge.domain.transfer_report import TransferDirection
from tests.unit.application.transfer_fakes import FakeRemoteHost
from tests.unit.application.use_cases.transfer_setup import (
    DEPLOY,
    TransferSetup,
    build,
)

TYPED = Confirmation(True, "100")


@pytest.fixture
def local(tmp_path: Path) -> list[str]:
    folder = tmp_path / "app"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.txt").write_bytes(b"new alpha")
    (folder / "sub" / "b.txt").write_bytes(b"beta")
    return [str(folder)]


def restore(
    setup: TransferSetup,
    local: list[str],
    tmp_path: Path,
    confirmation: Confirmation,
    destination: str = "/srv",
) -> TransferOutcome:
    result = setup.restore.request_restore(
        "Lab", "100", local, destination, str(tmp_path / "reports"), confirmation
    )
    return result.outcome


def pushes(setup: TransferSetup) -> int:
    return [c.operation for c in setup.host.calls].count("push")


def test_preview_lists_what_would_be_replaced_and_writes_nothing(
    local: list[str],
) -> None:
    setup = build()

    preview = setup.restore.preview_restore("Lab", "100", local, "/srv", None)

    assert preview.outcome is TransferOutcome.READY
    assert [o.path for o in preview.overwrites] == ["/srv/app"]
    assert preview.destination_exists is True
    assert preview.total_bytes == len(b"new alpha") + len(b"beta")
    assert setup.host.writes_on_server == []


def test_preview_of_a_new_destination_replaces_nothing(local: list[str]) -> None:
    preview = build().restore.preview_restore("Lab", "100", local, "/srv/new", None)

    assert preview.overwrites == ()
    assert preview.destination_exists is False


@pytest.mark.parametrize(
    "confirmation",
    [
        Confirmation(False),
        Confirmation(True),
        Confirmation(True, "web"),
        Confirmation(True, "10"),
        Confirmation(True, "1000"),
    ],
)
def test_nothing_is_pushed_without_the_exact_identifier(
    confirmation: Confirmation, local: list[str], tmp_path: Path
) -> None:
    setup = build()

    outcome = restore(setup, local, tmp_path, confirmation)

    assert outcome in {
        TransferOutcome.NOT_CONFIRMED,
        TransferOutcome.IDENTIFIER_MISMATCH,
    }
    assert pushes(setup) == 0
    assert setup.host.content("/srv/app/a.txt") == b"alpha"


def test_a_name_or_prefix_is_a_mismatch(local: list[str], tmp_path: Path) -> None:
    assert (
        restore(build(), local, tmp_path, Confirmation(True, "web"))
        is TransferOutcome.IDENTIFIER_MISMATCH
    )


def test_an_unmanaged_server_needs_the_identifier_typed_again(
    local: list[str], tmp_path: Path
) -> None:
    setup = build(managed=False)

    first = restore(setup, local, tmp_path, TYPED)
    wrong = restore(setup, local, tmp_path, Confirmation(True, "100", "101"))

    assert first is TransferOutcome.NOT_CONFIRMED
    assert wrong is TransferOutcome.IDENTIFIER_MISMATCH
    assert pushes(setup) == 0
    done = restore(setup, local, tmp_path, Confirmation(True, "100", "100"))
    assert done is TransferOutcome.COMPLETED


def test_a_confirmed_restore_replaces_verifies_and_saves_the_report(
    local: list[str], tmp_path: Path
) -> None:
    setup = build()

    result = setup.restore.request_restore(
        "Lab", "100", local, "/srv", str(tmp_path / "reports"), TYPED
    )

    assert result.outcome is TransferOutcome.COMPLETED
    assert setup.host.content("/srv/app/a.txt") == b"new alpha"
    report = result.report
    assert report is not None
    assert report.direction is TransferDirection.RESTORE
    assert report.verified
    assert [o.path for o in report.overwritten] == ["/srv/app"]
    assert report.report_file is not None
    assert Path(report.report_file).parent == tmp_path / "reports"
    assert pushes(setup) == 1


def test_one_changed_byte_is_a_verification_failure(
    local: list[str], tmp_path: Path
) -> None:
    setup = build(host=FakeRemoteHost(corrupt_in_transit=True))

    result = setup.restore.request_restore(
        "Lab", "100", local, "/srv", str(tmp_path / "r"), TYPED
    )

    assert result.outcome is TransferOutcome.VERIFICATION_FAILED
    assert result.report is not None
    assert result.report.verified is False
    assert result.report.differences != ()


def test_paths_that_are_not_valid_or_missing_are_refused(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    (tmp_path / "y" / "same").mkdir()
    (tmp_path / "x" / "same").mkdir()
    setup = build()
    same = [str(tmp_path / "x" / "same"), str(tmp_path / "y" / "same")]

    duplicate = setup.restore.preview_restore("Lab", "100", same, "/srv", None)
    relative = setup.restore.preview_restore(
        "Lab", "100", [str(tmp_path / "x")], "srv", None
    )
    missing = setup.restore.preview_restore(
        "Lab", "100", [str(tmp_path / "nope")], "/srv", None
    )

    assert duplicate.outcome is TransferOutcome.INVALID_PATH
    assert relative.outcome is TransferOutcome.INVALID_PATH
    assert missing.outcome is TransferOutcome.PATH_NOT_FOUND
    assert setup.host.calls == []


def test_a_server_that_is_not_running_is_refused(
    local: list[str], tmp_path: Path
) -> None:
    setup = build()
    setup.accesses.save(
        DEPLOY.__class__(DEPLOY.configuration_id, "101", "192.0.2.10", 22, "u", None)
    )

    result = setup.restore.request_restore(
        "Lab", "101", local, "/srv", str(tmp_path), Confirmation(True, "101")
    )

    assert result.outcome is TransferOutcome.SERVER_NOT_RUNNING
    assert setup.host.calls == []


class _FailingPush(FakeRemoteHost):
    def push(self, *arguments: object) -> list[object]:  # type: ignore[override]
        raise TransferFailedError("the connection broke")


def test_a_copy_that_stops_is_reported_as_incomplete(
    local: list[str], tmp_path: Path
) -> None:
    setup = build(host=_FailingPush())

    result = setup.restore.request_restore(
        "Lab", "100", local, "/srv", str(tmp_path / "r"), TYPED
    )

    assert result.outcome is TransferOutcome.TRANSFER_FAILED
    assert result.report is not None
    assert result.report.note is not None
    assert result.report.verified is False


def test_the_host_key_must_already_be_known_for_a_request(
    local: list[str], tmp_path: Path
) -> None:
    setup = build(host=FakeRemoteHost(start_known=False))

    outcome = restore(setup, local, tmp_path, TYPED)

    assert outcome is TransferOutcome.HOST_KEY_UNKNOWN
    assert pushes(setup) == 0
