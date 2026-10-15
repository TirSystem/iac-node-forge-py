"""``provider add``: validate access to a provider and save its configuration."""

import argparse

from iac_node_forge.application.use_cases import ConfigureProviderAccess
from iac_node_forge.domain.provider_kind import ProviderKind
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.commands import Subparsers
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.services import CliServices

_KINDS = (ProviderKind.PROXMOX_VE,)
_TOKEN_HELP = "Proxmox VE API token, as USER@REALM!TOKENID=SECRET"


def add_parser(subparsers: Subparsers) -> None:
    """Register ``provider add``."""
    parser = subparsers.add_parser(
        "add",
        help="validate access to a provider and save its configuration",
        description=(
            "Ask for the provider address and a display name (unless given), "
            f"ask for the API token without echo ({_TOKEN_HELP}), check that the "
            "provider accepts it and save the configuration. The token is never "
            "written to the configuration file."
        ),
    )
    parser.add_argument(
        "--kind",
        choices=[kind.value for kind in _KINDS],
        default=ProviderKind.PROXMOX_VE.value,
        help="provider kind (default: %(default)s)",
    )
    parser.add_argument("--address", help="provider address, such as https://host:8006")
    parser.add_argument("--name", help="display name of the configuration")
    parser.add_argument(
        "--cache-credential",
        action="store_true",
        help="keep the token in the platform credential store",
    )
    parser.set_defaults(handler=run)


def run(arguments: argparse.Namespace, services: CliServices) -> int:
    """Validate, then save; return an exit code."""
    kind = ProviderKind(arguments.kind)
    address = (arguments.address or services.read_line("Provider address: ")).strip()
    name = (arguments.name or services.read_line("Display name: ")).strip()
    if not address or not name:
        write_line(services.stderr, "error: the address and the name must not be empty")
        return exit_codes.FAILURE
    credentials = services.credential_prompt.ask(kind)
    use_case = ConfigureProviderAccess(
        services.provider_factory(kind, credentials),
        services.configurations,
        services.credential_store,
    )
    check = use_case.validate_access(kind, address, credentials)
    if not check.succeeded:
        write_line(
            services.stderr,
            f"error: access to {address} failed: {check.reason}\n"
            "Nothing was saved. Check the address, the token and its permissions.",
        )
        return exit_codes.FAILURE
    saved = use_case.save_provider_configuration(
        kind, address, name, arguments.cache_credential
    )
    write_line(services.stdout, f"Access to {address} works.")
    write_line(
        services.stdout,
        f"Saved configuration {saved.display_name!r} (id {saved.configuration_id}).",
    )
    if saved.credential_cached:
        write_line(
            services.stdout, "The token is kept in the platform credential store."
        )
    if saved.credential_cache_unavailable:
        write_line(
            services.stderr,
            "warning: no usable platform credential store; the token was not "
            "kept and will be asked for on each run.",
        )
    return exit_codes.SUCCESS
