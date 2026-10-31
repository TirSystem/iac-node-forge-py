"""The composition root: the one place that names the infrastructure classes."""

import sys
from collections.abc import Mapping
from typing import TextIO

from iac_node_forge.application.errors import UnsupportedProviderError
from iac_node_forge.application.ports import ProviderPort
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.infrastructure.configuration import (
    JsonLinesBackupStore,
    JsonLinesConfigurationStore,
    JsonLinesDesiredConfigurationStore,
    JsonLinesOperationStore,
    JsonLinesProvisionIntentStore,
    JsonLinesServerAccessStore,
    default_configuration_path,
)
from iac_node_forge.infrastructure.contabo import (
    DEFAULT_AUTH_URL,
    DEFAULT_RESTART_SETTLE_SECONDS,
    ContaboProvider,
)
from iac_node_forge.infrastructure.credentials import (
    ConsoleCredentialPrompt,
    PlatformCredentialStore,
)
from iac_node_forge.infrastructure.filesystem import LocalDiskFilesystem
from iac_node_forge.infrastructure.openssh import (
    OpenSshFileTransfer,
    OpenSshKnownHosts,
    OpenSshRemoteReader,
)
from iac_node_forge.infrastructure.proxmox import ProxmoxProvider
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.services import CliServices, TransferServices
from iac_node_forge.presentation.cli.settings import (
    VERIFY_ENV_VAR,
    read_tls_verification,
)


def build_services(
    environment: Mapping[str, str],
    stdout: TextIO,
    stderr: TextIO,
    contabo_auth_url: str | None = None,
    contabo_restart_settle_seconds: float | None = None,
) -> CliServices:
    """Create the real adapters for this environment.

    ``contabo_auth_url`` replaces the Contabo token endpoint; only a test
    passes it. It is deliberately not read from the environment, so a hostile
    environment cannot redirect the credentials. ``contabo_restart_settle_seconds``
    is likewise only a test's way to shorten the wait after a restart.

    Raises SettingsError when ``IAC_NODE_FORGE_VERIFY`` is not usable.
    """
    verify = read_tls_verification(environment)
    if verify is False:
        write_line(
            stderr,
            f"warning: {VERIFY_ENV_VAR}=false: the certificate of the provider "
            "is not verified",
        )

    def provider_factory(kind: ProviderKind, credentials: Credentials) -> ProviderPort:
        if kind is ProviderKind.PROXMOX_VE:
            return ProxmoxProvider(credentials, verify=verify)
        if kind is ProviderKind.CONTABO:
            return ContaboProvider(
                credentials,
                auth_url=contabo_auth_url or DEFAULT_AUTH_URL,
                verify=verify,
                restart_settle_seconds=(
                    DEFAULT_RESTART_SETTLE_SECONDS
                    if contabo_restart_settle_seconds is None
                    else contabo_restart_settle_seconds
                ),
            )
        raise UnsupportedProviderError(
            f"no adapter for {kind.value} in this version of the client"
        )

    path = default_configuration_path(environment, sys.platform)
    return CliServices(
        configurations=JsonLinesConfigurationStore(path),
        operations=JsonLinesOperationStore(path),
        desired_configurations=JsonLinesDesiredConfigurationStore(path),
        provision_intents=JsonLinesProvisionIntentStore(path),
        backups=JsonLinesBackupStore(path),
        credential_store=PlatformCredentialStore(),
        credential_prompt=ConsoleCredentialPrompt(),
        provider_factory=provider_factory,
        transfer=TransferServices(
            server_access=JsonLinesServerAccessStore(path),
            remote_reader=OpenSshRemoteReader(),
            file_transfer=OpenSshFileTransfer(),
            known_hosts=OpenSshKnownHosts(),
            local_filesystem=LocalDiskFilesystem(),
        ),
        read_line=input,
        stdout=stdout,
        stderr=stderr,
        is_interactive=_stdin_is_terminal(),
    )


def _stdin_is_terminal() -> bool:
    stream = sys.stdin
    return stream is not None and stream.isatty()
