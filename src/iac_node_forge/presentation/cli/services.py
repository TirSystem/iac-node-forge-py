"""The ports and streams a command needs, handed over by the composition root."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TextIO

from iac_node_forge.application.ports import (
    ConfigurationStore,
    CredentialPrompt,
    CredentialStore,
    DesiredConfigurationStore,
    FileTransferPort,
    KnownHostsPort,
    LocalFilesystemPort,
    OperationStore,
    ProviderPort,
    ProvisionIntentStore,
    RemoteReadPort,
    ServerAccessStore,
)
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_kind import ProviderKind

type ProviderFactory = Callable[[ProviderKind, Credentials], ProviderPort]
"""Builds the provider port of a kind for credentials; raises
UnsupportedProviderError when no adapter exists for the kind."""


def utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class TransferServices:
    """The ports behind server access, backup and restore (ADR-0009)."""

    server_access: ServerAccessStore
    remote_reader: RemoteReadPort
    file_transfer: FileTransferPort
    known_hosts: KnownHostsPort
    local_filesystem: LocalFilesystemPort


@dataclass(frozen=True, slots=True)
class CliServices:
    """Everything a command uses; fakes of it make a command testable.

    ``is_interactive`` is true when standard input is a terminal; only then may
    a command ask a yes/no question. ``clock`` and ``sleep`` are what polling an
    operation uses, so a test needs no real waiting.
    """

    configurations: ConfigurationStore
    operations: OperationStore
    desired_configurations: DesiredConfigurationStore
    provision_intents: ProvisionIntentStore
    credential_store: CredentialStore
    credential_prompt: CredentialPrompt
    provider_factory: ProviderFactory
    transfer: TransferServices
    read_line: Callable[[str], str]
    stdout: TextIO
    stderr: TextIO
    is_interactive: bool = False
    clock: Callable[[], datetime] = utc_now
    sleep: Callable[[float], None] = time.sleep
