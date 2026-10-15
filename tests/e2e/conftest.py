"""Fixtures: the real client against the mock Proxmox VE and Contabo APIs.

The composition root builds the real configuration store and provider adapter.
Only the console prompt is replaced (it would read the terminal) and the OS
keyring is an in-memory backend, so no test touches the real credential store.
"""

import io
import json
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from iac_node_forge.infrastructure.credentials import ConsoleCredentialPrompt
from iac_node_forge.presentation.cli.composition import build_services
from iac_node_forge.presentation.cli.main import main
from tests.contract.conftest import CONTABO_VALUES, TOKEN, mock_contabo, mock_proxmox
from tests.contract.mock_contabo import MockContaboServer
from tests.contract.mock_proxmox import MockProxmoxServer
from tests.unit.infrastructure.credentials.memory_keyring import (
    MemoryKeyring,
    memory_keyring,
)

__all__ = [
    "CliSession",
    "MultiProviderSession",
    "Result",
    "cli_session",
    "memory_keyring",
    "mock_contabo",
    "mock_proxmox",
    "multi_session",
    "reference_name",
]


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


@dataclass(slots=True)
class MultiProviderSession:
    """Runs commands of the real client against both mock providers.

    The console prompt answers by the name of the value asked for, from
    ``secrets``. The Contabo token endpoint is injected through the composition
    root, as ADR-0005 allows.
    """

    configuration_file: Path
    contabo: MockContaboServer
    proxmox: MockProxmoxServer
    keyring: MemoryKeyring
    secrets: dict[str, str] = field(default_factory=dict)
    prompted_names: list[str] = field(default_factory=list)

    def run(self, *argv: str) -> Result:
        out, err = io.StringIO(), io.StringIO()
        environment = {"IAC_NODE_FORGE_CONFIG": str(self.configuration_file)}
        real = build_services(
            environment, out, err, contabo_auth_url=self.contabo.auth_url
        )
        services = replace(
            real,
            credential_prompt=ConsoleCredentialPrompt(self._read_secret),
            read_line=_no_terminal,
        )
        return Result(main(list(argv), services), out.getvalue(), err.getvalue())

    def _read_secret(self, prompt: str) -> str:
        name = prompt.split(" for ", 1)[0]
        self.prompted_names.append(name)
        return self.secrets[name]


def _no_terminal(prompt: str) -> str:
    raise AssertionError(f"the client asked on the terminal: {prompt!r}")


@pytest.fixture
def multi_session(
    tmp_path: Path,
    mock_contabo: MockContaboServer,
    mock_proxmox: MockProxmoxServer,
    memory_keyring: MemoryKeyring,
) -> Iterator[MultiProviderSession]:
    yield MultiProviderSession(
        tmp_path / "configuration.jsonl",
        mock_contabo,
        mock_proxmox,
        memory_keyring,
        secrets={"token": TOKEN, **CONTABO_VALUES},
    )


def reference_name(configuration_text: str) -> str:
    """Return the credential reference name a configuration file holds."""
    for line in configuration_text.splitlines():
        row = json.loads(line)
        if row["record_type"] == "credential_reference":
            return str(row["name"])
    raise AssertionError("no credential reference in the configuration file")
