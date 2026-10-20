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
    FakeClock,
    FakeConfigurationStore,
    FakeCredentialPrompt,
    FakeCredentialStore,
    FakeDesiredConfigurationStore,
    FakeOperationStore,
    FakeProvider,
    FakeProvisionIntentStore,
)
from tests.unit.application.samples import SECRET
from tests.unit.application.transfer_fakes import FakeTransferBundle


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
    operations: FakeOperationStore = field(default_factory=FakeOperationStore)
    desired_configurations: FakeDesiredConfigurationStore = field(
        default_factory=FakeDesiredConfigurationStore
    )
    intents: FakeProvisionIntentStore = field(default_factory=FakeProvisionIntentStore)
    clock: FakeClock = field(default_factory=FakeClock)
    interactive: bool = False
    credential_store: FakeCredentialStore = field(default_factory=FakeCredentialStore)
    prompt: FakeCredentialPrompt = field(
        default_factory=lambda: FakeCredentialPrompt(SECRET)
    )
    providers_by_secret: dict[str, ProviderPort] = field(default_factory=dict)
    transfer: FakeTransferBundle = field(default_factory=FakeTransferBundle)
    answers: Iterable[str] = ()
    asked: list[str] = field(default_factory=list)
    secrets_used: list[str] = field(default_factory=list)

    def run(self, *argv: str) -> CliRun:
        out, err = io.StringIO(), io.StringIO()
        answers = iter(self.answers)

        def read_line(prompt: str) -> str:
            self.asked.append(prompt)
            try:
                return next(answers)
            except StopIteration:
                raise EOFError from None

        services = CliServices(
            configurations=self.configurations,
            operations=self.operations,
            desired_configurations=self.desired_configurations,
            provision_intents=self.intents,
            credential_store=self.credential_store,
            credential_prompt=self.prompt,
            provider_factory=self._factory,
            transfer=self.transfer.services(),
            read_line=read_line,
            stdout=out,
            stderr=err,
            is_interactive=self.interactive,
            clock=self.clock,
            sleep=self.clock.sleep,
        )
        code = main(list(argv), services)
        return CliRun(code, out.getvalue(), err.getvalue())

    @property
    def _factory(self) -> Callable[[ProviderKind, Credentials], ProviderPort]:
        def build(kind: ProviderKind, credentials: Credentials) -> ProviderPort:
            self.secrets_used.append(_first_value(credentials))
            return self.providers_by_secret.get(
                _first_value(credentials), self.provider
            )

        return build


def _first_value(credentials: Credentials) -> str:
    return credentials.get(credentials.names[0])
