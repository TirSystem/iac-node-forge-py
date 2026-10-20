"""UC-016: record how to reach a server, with fakes."""

from iac_node_forge.application.transfer_dto import AccessOutcome
from iac_node_forge.domain.server_access import ServerAccess
from tests.unit.application.samples import LAB_ID
from tests.unit.application.use_cases.transfer_setup import build


def test_the_access_is_kept_with_the_default_port() -> None:
    setup = build(access=None)

    view = setup.define.define_server_access("Lab", "100", None, None, "deploy", None)

    assert view.outcome is AccessOutcome.SAVED
    assert (view.host, view.port, view.user) == (None, 22, "deploy")
    assert setup.accesses.load(LAB_ID, "100") == ServerAccess(
        LAB_ID, "100", None, 22, "deploy", None
    )


def test_defining_again_replaces_the_access() -> None:
    setup = build(access=None)
    setup.define.define_server_access("Lab", "100", None, None, "deploy", None)

    setup.define.define_server_access(
        "Lab", "100", "10.0.0.5", 2222, "ops", "~/.ssh/id_ed25519"
    )

    kept = setup.accesses.load(LAB_ID, "100")
    assert kept == ServerAccess(
        LAB_ID, "100", "10.0.0.5", 2222, "ops", "~/.ssh/id_ed25519"
    )
    assert len(setup.accesses.saved) == 1


def test_a_value_that_is_not_valid_keeps_nothing_and_is_not_repeated() -> None:
    setup = build(access=None)
    key = "-----BEGIN OPENSSH PRIVATE KEY-----abc"
    results = [
        setup.define.define_server_access("Lab", "100", None, None, "", None),
        setup.define.define_server_access("Lab", "100", None, 70000, "u", None),
        setup.define.define_server_access("Lab", "100", "-oBad", None, "u", None),
        setup.define.define_server_access("Lab", "100", None, None, "u", key),
    ]

    assert {r.outcome for r in results} == {AccessOutcome.INVALID}
    assert all(key not in r.reason and "abc" not in r.reason for r in results)
    assert setup.accesses.saved == {}


def test_an_unknown_server_or_configuration_is_not_found() -> None:
    setup = build(access=None)

    server = setup.define.define_server_access("Lab", "999", None, None, "u", None)
    config = setup.define.define_server_access("Nope", "100", None, None, "u", None)

    assert server.outcome is AccessOutcome.NOT_FOUND
    assert config.outcome is AccessOutcome.NOT_FOUND
    assert setup.accesses.saved == {}
    assert setup.host.calls == []
