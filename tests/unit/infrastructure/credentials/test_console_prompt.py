import getpass

import pytest

from iac_node_forge.application.errors import EmptySecretError
from iac_node_forge.application.ports import CredentialPrompt
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.infrastructure.credentials import ConsoleCredentialPrompt


class _Reader:
    def __init__(self, *answers: str) -> None:
        self._answers = iter(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return next(self._answers)


def test_prompt_satisfies_the_port() -> None:
    assert isinstance(ConsoleCredentialPrompt(_Reader("x")), CredentialPrompt)


def test_ask_returns_the_token_for_proxmox() -> None:
    reader = _Reader("pa55")

    credentials = ConsoleCredentialPrompt(reader).ask(ProviderKind.PROXMOX_VE)

    assert credentials.names == ("token",)
    assert credentials.single_value() == "pa55"
    assert len(reader.prompts) == 1
    assert "token" in reader.prompts[0]
    assert "proxmox_ve" in reader.prompts[0]
    assert "pa55" not in repr(credentials)


def test_ask_asks_each_contabo_name_in_order() -> None:
    reader = _Reader("id-1", "secret-2", "user-3", "pw-4")

    credentials = ConsoleCredentialPrompt(reader).ask(ProviderKind.CONTABO)

    expected = ("client_id", "client_secret", "api_user", "api_password")
    assert credentials.names == expected
    assert [credentials.get(n) for n in expected] == [
        "id-1",
        "secret-2",
        "user-3",
        "pw-4",
    ]
    assert len(reader.prompts) == 4
    for name, prompt in zip(expected, reader.prompts, strict=True):
        assert name in prompt
        assert "contabo" in prompt
    assert not any(v in repr(credentials) for v in ("id-1", "secret-2", "pw-4"))


@pytest.mark.parametrize("answer", ["", "   "])
def test_ask_refuses_an_empty_answer(answer: str) -> None:
    with pytest.raises(EmptySecretError):
        ConsoleCredentialPrompt(_Reader(answer)).ask(ProviderKind.PROXMOX_VE)


def test_ask_refuses_an_empty_answer_to_a_later_name() -> None:
    reader = _Reader("id-1", "")

    with pytest.raises(EmptySecretError, match="client_secret") as caught:
        ConsoleCredentialPrompt(reader).ask(ProviderKind.CONTABO)

    assert "id-1" not in str(caught.value)
    assert len(reader.prompts) == 2


def test_default_reader_is_getpass() -> None:
    assert ConsoleCredentialPrompt()._read_secret is getpass.getpass
