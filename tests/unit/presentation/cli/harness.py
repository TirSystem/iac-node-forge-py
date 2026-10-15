"""Builds CliServices from fakes and runs ``main`` with captured output."""

import io
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from iac_node_forge.application.ports import ProviderPort
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.presentation.cli.main import main
from iac_node_forge.presentation.cli.services import CliServices
from tests.unit.application.fakes import (
    FakeConfigurationStore,
    FakeCredentialPrompt,
    FakeCredentialStore,
    FakeProvider,
)
from tests.unit.application.samples import SECRET


@dataclass(slots=True)
class CliRun:
    """The outcome of one ``main`` call."""

    code: int
    out: str
    err: str


@dataclass(slots=True)
class Harness:
    """Fakes behind the services of one test, and what the factory was given."""

    provider: ProviderPort = field(
        default_factory=lambda: FakeProvider(accepted_secret=SECRET)
    )
    configurations: FakeConfigurationStore = field(
        default_factory=FakeConfigurationStore
    )
    credential_store: FakeCredentialStore = field(default_factory=FakeCredentialStore)
    prompt: FakeCredentialPrompt = field(
        default_factory=lambda: FakeCredentialPrompt(SECRET)
    )
    providers_by_secret: dict[str, ProviderPort] = field(default_factory=dict)
    answers: Iterable[str] = ()
    secrets_used: list[str] = field(default_factory=list)

    def run(self, *argv: str) -> CliRun:
        out, err = io.StringIO(), io.StringIO()
        answers = iter(self.answers)
        services = CliServices(
            configurations=self.configurations,
            credential_store=self.credential_store,
            credential_prompt=self.prompt,
            provider_factory=self._factory,
            read_line=lambda _prompt: next(answers),
            stdout=out,
            stderr=err,
        )
        code = main(list(argv), services)
        return CliRun(code, out.getvalue(), err.getvalue())

    @property
    def _factory(self) -> Callable[[ProviderKind, Credentials], ProviderPort]:
        def build(kind: ProviderKind, credentials: Credentials) -> ProviderPort:
            self.secrets_used.append(credentials.secret)
            return self.providers_by_secret.get(credentials.secret, self.provider)

        return build
