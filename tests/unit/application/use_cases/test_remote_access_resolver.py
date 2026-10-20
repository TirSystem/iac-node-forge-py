"""The checks both transfers share, with fakes and no network."""

from iac_node_forge.application.errors import ConnectionFailedError
from iac_node_forge.application.transfer_dto import TransferOutcome
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_access import ServerAccess
from tests.unit.application.samples import LAB_ID
from tests.unit.application.transfer_fakes import OTHER_KEY, FakeRemoteHost
from tests.unit.application.use_cases.transfer_setup import HOST, build


def fingerprint_shown() -> str:
    return FakeRemoteHost().key.fingerprint


def test_resolve_is_ready_with_the_recorded_access_and_the_key() -> None:
    setup = build()

    found = setup.resolver.resolve("Lab", "100", None)

    assert found.outcome is TransferOutcome.READY
    assert found.managed is True
    assert found.access is not None
    assert (found.access.host, found.access.user) == (HOST, "deploy")
    assert found.host_key is not None
    assert found.host_key.fingerprint == fingerprint_shown()


def test_resolve_takes_the_first_provider_address_without_prefix_length() -> None:
    record = ServerAccess(LAB_ID, "100", None, 2222, "deploy", None)
    setup = build(access=record)

    found = setup.resolver.resolve("Lab", "100", None)

    assert found.access is not None
    assert (found.access.host, found.access.port) == (HOST, 2222)


def test_resolve_reports_a_server_the_provider_does_not_have() -> None:
    found = build().resolver.resolve("Lab", "999", None)

    assert found.outcome is TransferOutcome.SERVER_NOT_FOUND
    assert found.host_key is None


def test_resolve_reports_a_server_that_is_not_running() -> None:
    setup = build()
    setup.accesses.save(ServerAccess(LAB_ID, "101", HOST, 22, "deploy", None))

    found = setup.resolver.resolve("Lab", "101", None)

    assert found.outcome is TransferOutcome.SERVER_NOT_RUNNING
    assert setup.host.calls == []


def test_resolve_reports_missing_access() -> None:
    found = build(access=None).resolver.resolve("Lab", "100", None)

    assert found.outcome is TransferOutcome.ACCESS_MISSING
    assert "server access" in found.reason
    assert found.host_key is None


def test_resolve_reports_no_address_when_neither_record_nor_provider_has_one() -> None:
    record = ServerAccess(LAB_ID, "100", None, 22, "deploy", None)
    bare = Server("100", "web", "running", "2 vCPU", (), False)

    found = build(access=record, servers=(bare,)).resolver.resolve("Lab", "100", None)

    assert found.outcome is TransferOutcome.ACCESS_MISSING
    assert "no address" in found.reason


def test_an_unknown_host_key_stops_and_known_hosts_is_not_changed() -> None:
    setup = build(host=FakeRemoteHost(start_known=False))

    found = setup.resolver.resolve("Lab", "100", None)

    assert found.outcome is TransferOutcome.HOST_KEY_UNKNOWN
    assert found.host_key is not None
    assert found.host_key.fingerprint in found.reason
    assert setup.host.known == {}
    assert [call.operation for call in setup.host.calls] == ["check_host_key"]


def test_the_exact_fingerprint_is_trusted_and_written_once() -> None:
    setup = build(host=FakeRemoteHost(start_known=False))

    found = setup.resolver.resolve("Lab", "100", fingerprint_shown())
    again = setup.resolver.resolve("Lab", "100", None)

    assert found.outcome is TransferOutcome.READY
    assert again.outcome is TransferOutcome.READY
    assert [c.operation for c in setup.host.calls].count("trust") == 1


def test_another_fingerprint_is_refused_and_nothing_is_written() -> None:
    setup = build(host=FakeRemoteHost(start_known=False))

    found = setup.resolver.resolve("Lab", "100", OTHER_KEY.fingerprint)

    assert found.outcome is TransferOutcome.HOST_KEY_UNKNOWN
    assert "does not match" in found.reason
    assert setup.host.known == {}


def test_a_changed_host_key_is_never_accepted() -> None:
    fake = FakeRemoteHost()
    fake.change_key()
    setup = build(host=fake)

    found = setup.resolver.resolve("Lab", "100", fake.key.fingerprint)

    assert found.outcome is TransferOutcome.HOST_KEY_CHANGED
    assert "trust" not in [call.operation for call in fake.calls]


def test_a_failed_connection_is_reported_with_its_reason() -> None:
    fake = FakeRemoteHost(fail_with=ConnectionFailedError("connection refused"))

    found = build(host=fake).resolver.resolve("Lab", "100", None)

    assert found.outcome is TransferOutcome.CONNECTION_FAILED
    assert found.reason == "connection refused"
