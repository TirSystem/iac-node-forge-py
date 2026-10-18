"""ServerAccess, RemoteAccess and HostKey (ADR-0009)."""

import pytest

from iac_node_forge.domain.errors import EmptyValueError, InvalidServerAccessError
from iac_node_forge.domain.host_key import HostKey, HostKeyCheck, HostKeyStanding
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.server_access import (
    DEFAULT_PORT,
    MAX_IDENTITY_HINT_LENGTH,
    RemoteAccess,
    ServerAccess,
)

LAB = ConfigurationId("lab")


def access(**overrides: object) -> ServerAccess:
    values: dict[str, object] = {
        "configuration_id": LAB,
        "server_identifier": "100",
        "host": "web.example.org",
        "port": DEFAULT_PORT,
        "user": "deploy",
        "identity_hint": "~/.ssh/id_ed25519",
    }
    values.update(overrides)
    return ServerAccess(**values)  # type: ignore[arg-type]


def test_a_server_access_without_host_or_hint_is_valid() -> None:
    result = access(host=None, identity_hint=None)

    assert result.host is None
    assert result.identity_hint is None


@pytest.mark.parametrize(
    "host", ["10.0.0.5", "web-1.example.org", "::1", "fe80::1%eth0"]
)
def test_a_name_or_an_address_is_a_valid_host(host: str) -> None:
    assert access(host=host).host == host


@pytest.mark.parametrize(
    "host", ["", "web 1", "-oProxyCommand=x", "a;b", "a\nb", "a\x00b", "web$(id)"]
)
def test_a_host_with_a_space_or_a_leading_dash_is_refused(host: str) -> None:
    with pytest.raises(InvalidServerAccessError):
        access(host=host)


@pytest.mark.parametrize("port", [0, -1, 65536, 70000])
def test_a_port_outside_one_to_65535_is_refused(port: int) -> None:
    with pytest.raises(InvalidServerAccessError):
        access(port=port)


@pytest.mark.parametrize("port", [1, 22, 2222, 65535])
def test_a_port_from_one_to_65535_is_valid(port: int) -> None:
    assert access(port=port).port == port


def test_a_boolean_is_not_a_port() -> None:
    with pytest.raises(InvalidServerAccessError):
        access(port=True)


@pytest.mark.parametrize("user", ["", "  "])
def test_a_user_is_required(user: str) -> None:
    with pytest.raises(EmptyValueError):
        access(user=user)


@pytest.mark.parametrize("user", ["-oProxyCommand=x", "a b", "a\nb", "a;b"])
def test_a_user_with_a_space_or_a_leading_dash_is_refused(user: str) -> None:
    with pytest.raises(InvalidServerAccessError):
        access(user=user)


def test_an_identity_hint_over_500_characters_is_refused() -> None:
    with pytest.raises(InvalidServerAccessError):
        access(identity_hint="x" * (MAX_IDENTITY_HINT_LENGTH + 1))


def test_an_identity_hint_that_looks_like_a_private_key_is_refused_unrepeated() -> None:
    with pytest.raises(InvalidServerAccessError) as caught:
        access(identity_hint="-----BEGIN OPENSSH PRIVATE KEY-----")

    assert "BEGIN" not in str(caught.value)


def test_an_identity_hint_of_one_line_is_valid() -> None:
    assert access(identity_hint="agent key of the team").identity_hint


def test_a_remote_access_validates_like_a_server_access() -> None:
    with pytest.raises(InvalidServerAccessError):
        RemoteAccess("-bad", 22, "deploy", None)
    with pytest.raises(InvalidServerAccessError):
        RemoteAccess("web", 0, "deploy", None)
    with pytest.raises(EmptyValueError):
        RemoteAccess("web", 22, "", None)


def test_a_host_key_validates_its_parts() -> None:
    assert HostKey("web", 22, "ssh-ed25519", "SHA256:abc").fingerprint == "SHA256:abc"
    with pytest.raises(InvalidServerAccessError):
        HostKey("web", 0, "ssh-ed25519", "SHA256:abc")
    with pytest.raises(EmptyValueError):
        HostKey("web", 22, "ssh-ed25519", "")


def test_the_three_standings_exist() -> None:
    assert {s.name for s in HostKeyStanding} == {"KNOWN", "UNKNOWN", "CHANGED"}
    assert HostKeyCheck(HostKeyStanding.KNOWN, None).host_key is None
