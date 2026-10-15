"""Fixtures: the real client against the mock Proxmox VE API.

The composition root builds the real configuration store and provider adapter.
Only the console prompt is replaced (it would read the terminal) and the OS
keyring is an in-memory backend, so no test touches the real credential store.
"""

import io
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from iac_node_forge.infrastructure.credentials import ConsoleCredentialPrompt
from iac_node_forge.presentation.cli.composition import build_services
from iac_node_forge.presentation.cli.main import main
from tests.contract.conftest import TOKEN, mock_proxmox
from tests.contract.mock_proxmox import MockProxmoxServer
from tests.unit.infrastructure.credentials.memory_keyring import (
    MemoryKeyring,
    memory_keyring,
)

__all__ = ["CliSession", "Result", "cli_session", "memory_keyring", "mock_proxmox"]


@dataclass(frozen=True, slots=True)
class Result:
    """The exit code and the two streams of one command."""

    code: int
    out: str
    err: str


@dataclass(slots=True)
class CliSession:
    """Runs commands of the real client with a private configuration file."""

    configuration_file: Path
    server: MockProxmoxServer
    keyring: MemoryKeyring
    token: str = TOKEN
    answers: list[str] = field(default_factory=list)
    secret_prompts: int = 0

    def run(self, *argv: str) -> Result:
        out, err = io.StringIO(), io.StringIO()
        environment = {"IAC_NODE_FORGE_CONFIG": str(self.configuration_file)}
        real = build_services(environment, out, err)
        services = replace(
            real,
            credential_prompt=ConsoleCredentialPrompt(self._read_secret),
            read_line=lambda _prompt: self.answers.pop(0),
        )
        return Result(main(list(argv), services), out.getvalue(), err.getvalue())

    def _read_secret(self, _prompt: str) -> str:
        self.secret_prompts += 1
        return self.token


@pytest.fixture
def cli_session(
    tmp_path: Path, mock_proxmox: MockProxmoxServer, memory_keyring: MemoryKeyring
) -> Iterator[CliSession]:
    yield CliSession(tmp_path / "configuration.jsonl", mock_proxmox, memory_keyring)
