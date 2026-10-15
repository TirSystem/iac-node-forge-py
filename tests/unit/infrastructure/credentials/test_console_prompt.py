import pytest

from iac_node_forge.application.errors import EmptySecretError
from iac_node_forge.application.ports import CredentialPrompt
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.infrastructure.credentials import ConsoleCredentialPrompt


class _Reader:
    def __init__(self, answer: str) -> None:
        self._answer = answer
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._answer


def test_prompt_satisfies_the_port() -> None:
    assert isinstance(ConsoleCredentialPrompt(_Reader("x")), CredentialPrompt)


def test_ask_returns_credentials_from_the_reader() -> None:
    reader = _Reader("pa55")

    credentials = ConsoleCredentialPrompt(reader).ask(ProviderKind.CONTABO)

    assert credentials.secret == "pa55"
    assert len(reader.prompts) == 1
    assert "contabo" in reader.prompts[0]
    assert "pa55" not in repr(credentials)


@pytest.mark.parametrize("answer", ["", "   "])
def test_ask_refuses_an_empty_answer(answer: str) -> None:
    with pytest.raises(EmptySecretError):
        ConsoleCredentialPrompt(_Reader(answer)).ask(ProviderKind.PROXMOX_VE)


def test_default_reader_is_getpass() -> None:
    import getpass

    assert ConsoleCredentialPrompt()._read_secret is getpass.getpass
