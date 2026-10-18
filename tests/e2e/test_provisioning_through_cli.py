"""Defining and provisioning end to end: real composition root, both mocks.

Each test runs for Proxmox VE and for Contabo. They show the acceptance
criteria of US-001.03 (define a desired configuration) and US-001.04
(provision a server) and the Go criteria of MIL-005 for the confirmation, the
duplicate protection, the write allow-list and the absence of secrets.
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest

from tests.contract.conftest import (
    CONTABO_ALLOWED_WRITES,
    CONTABO_CREATE_WRITE,
    CONTABO_VALUES,
    CONTABO_WRONG_VALUES,
    EXPECTED_IMAGES,
    EXPECTED_TEMPLATES,
    PROXMOX_ALLOWED_WRITES,
    TOKEN,
    WRONG_TOKEN,
)
from tests.e2e.conftest import MultiProviderSession, Result

FAST = ("--poll-interval", "0.02", "--timeout", "10")
SHORT = ("--poll-interval", "0.05", "--timeout", "0.3")
PROVIDERS = ["proxmox", "contabo"]
NEVER = 10**9
SERVER = "forge-1"


@dataclass(slots=True)
class Target:
    """One provider behind the CLI, with the hooks its mock offers."""

    session: MultiProviderSession
    name: str
    image: str
    settings: dict[str, str]
    bad_choice: tuple[str, str]
    cost_text: str
    secret_values: tuple[str, ...]
    allowed_writes: frozenset[tuple[str, str]]
    create_write: Callable[[], tuple[str, str]]
    writes: Callable[[], list[tuple[str, str]]]
    creates: Callable[[], int]
    state_at_provider: Callable[[str], str | None]
    reject_creation: Callable[[], None]
    fail_operations: Callable[[], None]
    never_finish: Callable[[], None]
    lose_answer: Callable[[], None]
    use_wrong_credentials: Callable[[], None]

    def define(self, name: str = "small", **overrides: str) -> Result:
        flags = [
            arg
            for pair in {**self.settings, **overrides}.items()
            for arg in ("--set", f"{pair[0]}={pair[1]}")
        ]
        return self.session.run(
            "server",
            "define",
            self.name,
            name,
            "--image",
            self.image,
            "--key",
            "deploy",
            "--hint",
            "install nginx",
            *flags,
        )

    def define_with_bad_choice(self, name: str = "small") -> Result:
        return self.define(name, **dict([self.bad_choice]))

    def provision(self, *extra: str) -> Result:
        return self.session.run("server", "provision", self.name, "small", *extra)

    def confirmed(self, *extra: str) -> Result:
        return self.provision("--yes", *extra)

    def file_rows(self, record_type: str) -> list[dict[str, object]]:
        text = self.session.configuration_file.read_text("utf-8")
        rows = [json.loads(line) for line in text.splitlines()]
        return [r for r in rows if r["record_type"] == record_type]

    def file_text(self) -> str:
        path = self.session.configuration_file
        return path.read_text("utf-8") if path.exists() else ""

    def operation_id(self, result: Result) -> str:
        for line in result.out.splitlines():
            if line.startswith("Operation:"):
                return line.split(":", 1)[1].strip()
        raise AssertionError(f"no operation in the output: {result.out!r}")

    def assert_no_secret(self, *texts: str) -> None:
        for text in texts:
            for value in self.secret_values:
                assert value not in text


def _proxmox_target(session: MultiProviderSession) -> Target:
    mock = session.proxmox
    added = session.run("provider", "add", "--address", mock.address, "--name", "Lab")
    assert added.code == 0, added.err

    def state_at_provider(name: str) -> str | None:
        return next(
            (str(g["status"]) for g in mock.state.resources if g["name"] == name),
            None,
        )

    def use_wrong() -> None:
        session.secrets["token"] = WRONG_TOKEN

    return Target(
        session,
        "Lab",
        EXPECTED_TEMPLATES[0].value,
        {
            "kind": "lxc",
            "node": "pve1",
            "storage": "pve1/local-lvm",
            "memory_mb": "512",
            "cores": "1",
            "disk_gb": "4",
        },
        ("node", "pve9"),
        "no commitment",
        (TOKEN, WRONG_TOKEN, TOKEN.split("=", 1)[1], WRONG_TOKEN.split("=", 1)[1]),
        PROXMOX_ALLOWED_WRITES,
        lambda: ("POST", "/api2/json/nodes/pve1/lxc"),
        lambda: [(r.method, r.path) for r in mock.write_requests],
        lambda: len(mock.state.created),
        state_at_provider,
        lambda: setattr(mock.state, "create_forced_status", 403),
        lambda: setattr(
            mock.state, "task_exitstatus", "unable to create CT 102 - no space"
        ),
        lambda: setattr(mock.state, "task_polls_before_end", NEVER),
        lambda: setattr(mock.state, "create_answers_without_task", True),
        use_wrong,
    )


def _contabo_target(session: MultiProviderSession) -> Target:
    mock = session.contabo
    added = session.run(
        "provider",
        "add",
        "--kind",
        "contabo",
        "--address",
        mock.address,
        "--name",
        "Cloud",
    )
    assert added.code == 0, added.err

    def state_at_provider(name: str) -> str | None:
        return next(
            (
                str(i["status"])
                for i in mock.state.instances
                if i["displayName"] == name
            ),
            None,
        )

    return Target(
        session,
        "Cloud",
        EXPECTED_IMAGES[0].value,
        {"region": "EU1", "product": "V45", "period": "12"},
        ("period", "6"),
        "12 months",
        tuple(CONTABO_VALUES.values()) + (CONTABO_WRONG_VALUES["api_password"],),
        CONTABO_ALLOWED_WRITES,
        lambda: CONTABO_CREATE_WRITE,
        lambda: [(r.method, r.path) for r in mock.write_requests],
        lambda: len(mock.state.created),
        state_at_provider,
        lambda: setattr(mock.state, "create_forced_status", 403),
        lambda: setattr(mock.state, "transition_fails", True),
        lambda: setattr(mock.state, "transition_reads", NEVER),
        lambda: setattr(mock.state, "create_answers_without_instance", True),
        lambda: session.secrets.update(CONTABO_WRONG_VALUES),
    )


@pytest.fixture(params=PROVIDERS)
def target(
    request: pytest.FixtureRequest, multi_session: MultiProviderSession
) -> Iterator[Target]:
    if request.param == "proxmox":
        yield _proxmox_target(multi_session)
    else:
        yield _contabo_target(multi_session)


def test_a_valid_definition_is_saved_as_valid_under_a_name(target: Target) -> None:
    """US-001.03: given known capabilities, a supported size and image is saved."""
    result = target.define("small")
    listed = target.session.run("server", "definitions", target.name, "--check")

    assert result.code == 0, result.err
    assert "'small' is valid and saved" in result.out
    assert listed.code == 0, listed.err
    assert "small" in listed.out
    assert "yes" in listed.out
    [saved] = target.file_rows("desired_configuration")
    assert (saved["desired_name"], saved["image"]) == ("small", target.image)
    [key] = target.file_rows("access_key_name")
    assert key["key_name"] == "deploy"
    assert target.file_rows("desired_configuration_value")
    assert target.writes() == []


def test_a_choice_the_provider_does_not_offer_is_reported_and_not_saved(
    target: Target,
) -> None:
    """US-001.03: given an unsupported choice, it is reported, not saved as valid."""
    bad_value = target.define_with_bad_choice()
    bad_image = target.session.run(
        "server",
        "define",
        target.name,
        "small",
        "--image",
        "no-such-image",
        *[
            arg
            for pair in target.settings.items()
            for arg in ("--set", f"{pair[0]}={pair[1]}")
        ],
    )
    listed = target.session.run("server", "definitions", target.name)

    assert bad_value.code == 1
    assert "not valid and was not saved" in bad_value.err
    assert target.bad_choice[1] in bad_value.err
    assert bad_image.code == 1
    assert "no-such-image" in bad_image.err
    assert target.file_rows("desired_configuration") == []
    assert "No desired configurations" in listed.out
    assert target.writes() == []


def test_a_definition_with_the_same_name_replaces_the_saved_one(target: Target) -> None:
    """US-001.03: the description is saved under a name, and the name is unique."""
    target.define("small")
    again = target.define("small")

    assert again.code == 0, again.err
    assert len(target.file_rows("desired_configuration")) == 1
    assert len(target.file_rows("access_key_name")) == 1


def test_the_preview_shows_what_is_created_and_the_cost_and_sends_nothing(
    target: Target,
) -> None:
    """US-001.04: shown what will be created and its cost; confirm before sending."""
    target.define("small")
    session = target.session

    without_yes = target.provision()
    session.interactive = True
    session.answers[:] = ["no"]
    refused = target.provision(*FAST)

    for result in (without_yes, refused):
        assert result.code == 1
        assert "About to create server 'small'" in result.out
        assert target.image in result.out
        assert target.cost_text in result.out
        assert "Provider:" in result.out
        assert "Target:" in result.out
        assert "Size:" in result.out
    assert "--yes" in without_yes.err
    assert "not confirmed" in refused.err
    assert "Do you want to create server 'small'" in session.questions[-1]
    assert target.writes() == []
    assert target.creates() == 0
    assert target.file_rows("provision_intent") == []
    assert target.file_rows("operation") == []


def test_the_contract_period_is_part_of_what_is_confirmed(target: Target) -> None:
    """US-001.04: the cost consequence names the commitment before anything is sent."""
    target.define("small")

    shown = target.provision()

    if target.name == "Cloud":
        assert "period=12" in shown.out
        assert "contract period of 12 months" in shown.out
        assert "commits the account" in shown.out
    else:
        assert "no commitment" in shown.out


def test_a_confirmed_request_ends_with_the_server_managed_and_the_outcome_recorded(
    target: Target,
) -> None:
    """US-001.04: given a confirmed request, the server is recorded as managed."""
    target.define("small")

    done = target.confirmed(*FAST)

    assert done.code == 0, done.err
    assert "Managed: server" in done.out
    assert "succeeded" in done.out
    assert target.writes() == [target.create_write()]
    created = target.state_at_provider("small")
    assert created is not None
    [managed] = target.file_rows("managed_server")
    [operation] = target.file_rows("operation")
    assert (operation["action"], operation["state"]) == ("provision", "succeeded")
    assert operation["server_identifier"] == managed["server_identifier"]
    [intent] = target.file_rows("provision_intent")
    assert intent["state"] == "ended"
    assert intent["operation_id"] == operation["operation_id"]
    listing = target.session.run("servers")
    assert "small" in listing.out
    line = next(ln for ln in listing.out.splitlines() if " small " in f"{ln} ")
    assert line.rstrip().endswith("managed")
    assert not line.rstrip().endswith("not managed")


def test_a_named_server_is_created_with_that_name(target: Target) -> None:
    """US-001.04: --name names the server; the description stays a template."""
    target.define("small")

    first = target.confirmed("--name", SERVER, *FAST)
    second = target.confirmed("--name", "forge-2", *FAST)

    assert first.code == 0, first.err
    assert second.code == 0, second.err
    assert target.state_at_provider(SERVER) is not None
    assert target.state_at_provider("forge-2") is not None
    assert target.creates() == 2
    assert len(target.file_rows("managed_server")) == 2


def test_a_request_can_be_followed_later_and_then_completed(target: Target) -> None:
    """US-001.04: operation --follow ends the operation and records the server."""
    target.define("small")
    started = target.confirmed("--no-wait")
    operation_id = target.operation_id(started)
    managed_before = target.file_rows("managed_server")

    followed = target.session.run("operation", operation_id, "--follow", *FAST)
    shown = target.session.run("operation", operation_id)

    assert started.code == 0, started.err
    assert managed_before == []
    assert followed.code == 0, followed.err
    assert "Managed: server" in followed.out
    assert shown.code == 0
    assert len(target.file_rows("managed_server")) == 1
    assert target.creates() == 1


def test_a_rejected_request_reports_the_state_left_and_is_not_repeated(
    target: Target,
) -> None:
    """US-001.04: given a failed request, the state left at the provider is reported."""
    target.define("small")
    target.reject_creation()

    result = target.confirmed(*FAST)
    shown = target.session.run("operation", target.operation_id(result))

    assert result.code == 1
    assert "rejected the creation" in result.err
    assert "State left at the provider: no server named 'small'" in result.err
    assert "failed" in shown.out
    assert target.writes() == [target.create_write()]
    assert target.file_rows("managed_server") == []
    [intent] = target.file_rows("provision_intent")
    assert intent["state"] == "ended"


def test_a_failed_operation_reports_the_state_left_at_the_provider(
    target: Target,
) -> None:
    """US-001.04: given a request that fails later, the state left is reported."""
    target.define("small")
    target.fail_operations()

    result = target.confirmed(*FAST)

    assert result.code == 1
    assert "failed" in result.err
    assert "State left at the provider:" in result.err
    assert "None" not in result.err
    assert target.file_rows("managed_server") == []
    [intent] = target.file_rows("provision_intent")
    assert intent["state"] == "ended"
    [operation] = target.file_rows("operation")
    assert operation["state"] == "failed"
    assert target.writes() == [target.create_write()]


def test_a_repeat_after_a_time_limit_sends_no_second_create_request(
    target: Target,
) -> None:
    """MIL-005 Go criterion: an unknown outcome is not followed by a second server."""
    target.define("small")
    target.never_finish()

    first = target.confirmed(*SHORT)
    second = target.confirmed(*FAST)

    assert first.code == 1
    assert "unknown" in first.out
    assert "None" not in first.err
    assert second.code == 1
    assert "not finished" in second.err or "already exists" in second.err
    assert target.creates() == 1
    assert target.writes() == [target.create_write()]
    [intent] = target.file_rows("provision_intent")
    assert intent["state"] == "pending"
    assert target.file_rows("managed_server") == []


def test_a_repeat_after_a_lost_answer_sends_no_second_create_request(
    target: Target,
) -> None:
    """MIL-005 Go criterion: a lost answer is not followed by a second server."""
    target.define("small")
    target.lose_answer()

    first = target.confirmed(*SHORT)
    second = target.confirmed(*FAST)

    assert first.code == 1
    assert "None" not in first.err
    assert second.code == 1
    assert "not finished" in second.err or "already exists" in second.err
    assert target.creates() == 1
    assert target.writes() == [target.create_write()]
    assert target.file_rows("managed_server") == []


def test_a_server_name_that_exists_at_the_provider_is_refused(target: Target) -> None:
    """US-001.04: a name that exists is reported and nothing is sent."""
    target.define("small")

    result = target.confirmed("--name", "web", *FAST)

    assert result.code == 1
    assert "already exists" in result.err
    assert target.writes() == []
    assert target.file_rows("provision_intent") == []


def test_only_the_allowed_write_requests_reach_the_provider(target: Target) -> None:
    """MIL-004 Go criterion 5, extended: the create request joins the allow-list."""
    target.define("small")
    target.provision()
    target.confirmed("--name", "forge-a", *FAST)
    target.confirmed("--name", "forge-b", "--no-wait")
    target.confirmed("--name", "web")
    target.session.run("server", "definitions", target.name, "--check")

    writes = target.writes()

    assert len(writes) == 2
    assert set(writes) == {target.create_write()}
    assert set(writes) <= target.allowed_writes


def test_no_credential_value_appears_in_a_record_a_file_or_an_error(
    target: Target,
) -> None:
    """MIL-005 Go criterion: no secret in any record, file, message or output."""
    session = target.session
    results = [
        target.define("small"),
        target.define_with_bad_choice("bad"),
        target.session.run("server", "definitions", target.name, "--check"),
        target.provision(),
        target.confirmed(*FAST),
        target.confirmed("--name", "web"),
    ]
    target.reject_creation()
    results.append(target.confirmed("--name", "forge-r", *FAST))
    target.fail_operations()
    target.use_wrong_credentials()
    results.append(target.confirmed("--name", "forge-w", *FAST))
    results.append(session.run("servers"))

    file_text = target.file_text()

    for record_type in (
        "desired_configuration",
        "access_key_name",
        "provision_intent",
        "managed_server",
        "operation",
    ):
        assert record_type in file_text
    target.assert_no_secret(file_text)
    for result in results:
        target.assert_no_secret(result.out, result.err)
        assert "Traceback" not in result.err
