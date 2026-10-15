"""The composition root: the one place that names the infrastructure classes."""

import sys
from collections.abc import Mapping
from typing import TextIO

from iac_node_forge.application.errors import UnsupportedProviderError
from iac_node_forge.application.ports import ProviderPort
from iac_node_forge.domain.credentials import Credentials
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.infrastructure.configuration import (
    JsonLinesConfigurationStore,
    default_configuration_path,
)
from iac_node_forge.infrastructure.contabo import DEFAULT_AUTH_URL, ContaboProvider
from iac_node_forge.infrastructure.credentials import (
    ConsoleCredentialPrompt,
    PlatformCredentialStore,
)
from iac_node_forge.infrastructure.proxmox import ProxmoxProvider
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.services import CliServices
from iac_node_forge.presentation.cli.settings import (
    VERIFY_ENV_VAR,
    read_tls_verification,
)


def build_services(
    environment: Mapping[str, str],
    stdout: TextIO,
    stderr: TextIO,
    contabo_auth_url: str | None = None,
) -> CliServices:
    """Create the real adapters for this environment.

    ``contabo_auth_url`` replaces the Contabo token endpoint; only a test
    passes it. It is deliberately not read from the environment, so a hostile
    environment cannot redirect the credentials.

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
            )
        raise UnsupportedProviderError(
            f"no adapter for {kind.value} in this version of the client"
        )

    return CliServices(
        configurations=JsonLinesConfigurationStore(
            default_configuration_path(environment, sys.platform)
        ),
        credential_store=PlatformCredentialStore(),
        credential_prompt=ConsoleCredentialPrompt(),
        provider_factory=provider_factory,
        read_line=input,
        stdout=stdout,
        stderr=stderr,
    )
