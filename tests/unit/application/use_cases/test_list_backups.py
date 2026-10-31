"""UC-019: list the backups of a server, merged with the records, with fakes."""

from dataclasses import dataclass
from datetime import UTC, datetime

from iac_node_forge.application.backup_dto import BackupOutcome
from iac_node_forge.application.use_cases._backup_lookup import resolve_backup
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.backup_kind import BackupKind
from iac_node_forge.domain.backup_record import BackupRecord
from iac_node_forge.domain.operation import OperationId
from tests.unit.application.samples import LAB_ID
from tests.unit.application.use_cases.backup_setup import build

NOW = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _SameAlias(Backup):
    """A backup whose short alias collides with every other of its kind."""

    def alias(self) -> str:
        return "aaaaaaaa"


def test_the_provider_list_is_shown_with_the_operation_that_made_a_backup() -> None:
    setup = build()
    made = setup.made_backup()
    foreign = setup.add_foreign("101", "local:backup/other.tar", hours=1)

    listing = setup.listing.list_backups("Lab", "101")

    assert listing.outcome is BackupOutcome.LISTED
    assert (listing.provider_name, listing.configuration_id) == ("Lab", LAB_ID)
    assert listing.server_identifier == "101"
    assert listing.kind is BackupKind.ARCHIVE
    assert [b.backup_id for b in listing.backups] == [made.backup_id, foreign.backup_id]
    mine, theirs = listing.backups
    assert mine.made_by_client
    [record] = setup.backups.saved
    assert mine.operation_id == record.operation_id
    assert mine.alias == made.alias()
    assert not theirs.made_by_client
    assert theirs.operation_id is None
    assert listing.gone_from_provider == ()


def test_a_recorded_backup_the_provider_no_longer_lists_is_reported_apart() -> None:
    setup = build()
    setup.made_backup()
    setup.backups.save(
        BackupRecord(
            LAB_ID,
            "101",
            "vanished",
            BackupKind.ARCHIVE,
            "storage local, volume vanished",
            NOW,
            OperationId("op-old"),
        )
    )

    listing = setup.listing.list_backups("Lab", "101")

    assert [b.backup_id for b in listing.backups] != ["vanished"]
    [gone] = listing.gone_from_provider
    assert gone.backup_id == "vanished"
    assert gone.operation_id == "op-old"
    assert gone.location == "storage local, volume vanished"
    assert len(setup.backups.records) == 2


def test_records_of_another_server_are_not_listed() -> None:
    setup = build()
    setup.made_backup("100")

    listing = setup.listing.list_backups("Lab", "101")

    assert listing.backups == ()
    assert listing.gone_from_provider == ()


def test_a_server_without_backups_lists_nothing() -> None:
    listing = build().listing.list_backups("Lab", "101")

    assert listing.outcome is BackupOutcome.LISTED
    assert listing.backups == ()


def test_listing_writes_nothing() -> None:
    setup = build()
    setup.made_backup()
    saved = (
        len(setup.backups.saved),
        len(setup.operations.saved),
        len(setup.fake.requests),
    )

    setup.listing.list_backups("Lab", "101")

    assert (
        len(setup.backups.saved),
        len(setup.operations.saved),
        len(setup.fake.requests),
    ) == saved


def test_an_unsupported_provider_lists_nothing() -> None:
    listing = build(supported=False).listing.list_backups("Lab", "101")

    assert listing.outcome is BackupOutcome.UNSUPPORTED
    assert "backup_server" in listing.reason


def test_a_server_the_provider_does_not_have_is_not_found() -> None:
    listing = build().listing.list_backups("Lab", "999")

    assert listing.outcome is BackupOutcome.NOT_FOUND


def _backup(backup_id: str, cls: type[Backup] = Backup) -> Backup:
    return cls(backup_id, BackupKind.ARCHIVE, "somewhere", NOW)


def test_an_identifier_is_resolved_before_an_alias() -> None:
    first, second = _backup("one"), _backup("two")

    assert resolve_backup([first, second], "two") is second
    assert resolve_backup([first, second], f"  {first.alias()}  ") is first
    assert resolve_backup([first, second], first.alias().upper()) is first


def test_nothing_is_resolved_for_an_unknown_text() -> None:
    assert resolve_backup([_backup("one")], "two") is None
    assert resolve_backup([], "one") is None
    assert resolve_backup([_backup("one")], "") is None


def test_an_alias_that_fits_several_backups_is_not_resolved() -> None:
    twins = [_backup("one", _SameAlias), _backup("two", _SameAlias)]

    assert resolve_backup(twins, "aaaaaaaa") is None
    assert resolve_backup(twins, "one") is twins[0]
