"""Live provisioning and removal test: it CREATES A REAL CONTAINER, then removes it.

Only Proxmox VE is tested live. Contabo is never created live: a Contabo
instance costs money and has a minimum term.

Nothing runs unless every variable is set: ``PVE_TOKEN`` (full
``USER@REALM!TOKENID=SECRET``), ``PVE_PROVISION_TEST_NODE`` (the node name),
``PVE_PROVISION_TEST_STORAGE`` (the storage of the root disk, for example
``local``), ``PVE_PROVISION_TEST_TEMPLATE`` (a container template volume id on
that node, for example ``local:vztmpl/alpine-3.24-default_20260714_amd64.tar.xz``)
and ``PVE_PROVISION_TEST_CONFIRM``, which must equal
``create-server-iacnf-live``. ``PVE_ADDRESS`` and ``PVE_VERIFY`` are optional,
as in ``test_proxmox_live.py``. Run it only against a disposable Proxmox VE.

The container is named ``iacnf-live-<8 hex>``, new on every run. The test
removes it through the product (``RemoveServer``: preview, request with the
typed identifier, track, complete). When the test fails before that, a
finalizer removes this run's container the same way, and only that one; the
product does not stop a server, so a running one is reported by id for removal
by hand. The test sends no raw delete. No credential value is printed, logged
or put in an assertion message. The helpers are shared with
``test_full_backup_live.py`` and live in ``_live_proxmox.py``.
"""

from collections.abc import Iterator

import pytest

from iac_node_forge.application.dto import Confirmation, ProvisionOutcome
from iac_node_forge.infrastructure.configuration import JsonLinesConfigurationStore
from tests.integration._live_proxmox import (
    ProvisionTarget,
    create_container,
    live_target,
    record_writes,
    remove_through_product,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def target(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ProvisionTarget]:
    yield from live_target(tmp_path_factory)


@pytest.fixture
def writes_sent(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Record every request that is neither a read nor a token request."""
    for log in record_writes(monkeypatch):
        yield log.product


def test_provision_proxmox_lxc_creates_records_and_refuses_duplicate(
    target: ProvisionTarget, writes_sent: list[str]
) -> None:
    configurations = JsonLinesConfigurationStore(target.store_path)
    configuration_id = target.configuration.configuration_id
    name = target.run_name
    seen: list[str] = []

    identifier, provisioner = create_container(target, writes_sent, seen)

    recorded = configurations.managed_servers(configuration_id)
    assert [r.server_identifier for r in recorded] == [identifier]
    listed = target.provider.list_servers(target.configuration)
    assert [s.server_identifier for s in listed if s.name == name] == [identifier]

    before = len(writes_sent)
    again = provisioner.request_provisioning(
        configuration_id, name, "", Confirmation(given=True)
    )
    seen.append(repr(again))
    assert again.outcome is ProvisionOutcome.NAME_EXISTS, again.outcome
    assert len(writes_sent) == before

    seen.extend(remove_through_product(target, writes_sent, identifier))
    assert writes_sent == ["POST", "DELETE"]

    seen.append(target.store_path.read_text(encoding="utf-8"))
    leaked = any(target.leaks(text) for text in seen)
    assert not leaked, "a stored or reported text contains a credential value"
