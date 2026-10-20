"""What ``server access|backup|restore`` share: wiring, preview and failure text."""

import argparse
from collections.abc import Sequence

from iac_node_forge.application.transfer_dto import (
    TransferOutcome,
    TransferPreview,
    TransferResult,
)
from iac_node_forge.application.use_cases import (
    BackUpFiles,
    DefineServerAccess,
    RemoteAccessResolver,
    RestoreFiles,
)
from iac_node_forge.domain.host_key import HostKey
from iac_node_forge.domain.transfer_report import TransferReport
from iac_node_forge.presentation.cli import exit_codes
from iac_node_forge.presentation.cli.operation_output import write_detail_line
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.provider_resolver import (
    CliProviderResolver,
    build_resolver,
)
from iac_node_forge.presentation.cli.services import CliServices

PROGRAM = "iac-node-forge server"
ACCEPT_HOST_KEY = "--accept-host-key"
_ACCEPT_HELP = (
    "accept the host key with this fingerprint (SHA256:...) when the server is "
    "not in known_hosts yet; one line is added to your known_hosts"
)
_MAX_LISTED = 20


def add_accept_host_key(parser: argparse.ArgumentParser) -> None:
    """Add ``--accept-host-key`` to a transfer command."""
    parser.add_argument(
        ACCEPT_HOST_KEY, metavar="FINGERPRINT", default=None, help=_ACCEPT_HELP
    )


def is_verb_request(argv: Sequence[str], verb: str) -> bool:
    """Whether ``argv`` is ``server <verb> ...`` and not ``server <cfg> <id>``.

    ``server backup lab`` with exactly three words is the read command for a
    configuration that is named ``backup``; the verbs need more words.
    """
    if len(argv) < 2 or argv[0] != "server" or argv[1] != verb:
        return False
    return len(argv) != 3 or argv[2].startswith("-")


def build_access_resolver(
    services: CliServices, providers: CliProviderResolver
) -> RemoteAccessResolver:
    """Wire the resolver of both transfers."""
    transfer = services.transfer
    return RemoteAccessResolver(
        providers,
        services.configurations,
        transfer.server_access,
        transfer.remote_reader,
        transfer.known_hosts,
    )


def build_back_up(services: CliServices) -> BackUpFiles:
    """Wire BackUpFiles for a command."""
    transfer = services.transfer
    return BackUpFiles(
        build_access_resolver(services, build_resolver(services)),
        transfer.remote_reader,
        transfer.file_transfer,
        transfer.local_filesystem,
        services.clock,
    )


def build_restore(services: CliServices) -> RestoreFiles:
    """Wire RestoreFiles for a command."""
    transfer = services.transfer
    return RestoreFiles(
        build_access_resolver(services, build_resolver(services)),
        transfer.remote_reader,
        transfer.file_transfer,
        transfer.local_filesystem,
        services.clock,
    )


def build_define_access(services: CliServices) -> DefineServerAccess:
    """Wire DefineServerAccess for a command."""
    return DefineServerAccess(
        build_resolver(services),
        services.configurations,
        services.transfer.server_access,
    )


def write_preview(services: CliServices, preview: TransferPreview) -> None:
    """Print the facts of a transfer as far as the checks got."""
    out = services.stdout
    if preview.server_state is not None:
        name = f" ({preview.server_name})" if preview.server_name else ""
        write_detail_line(out, "Provider", preview.provider_name or "-")
        write_detail_line(out, "Server", f"{preview.server_identifier}{name}")
        write_detail_line(out, "State", preview.server_state)
        write_detail_line(out, "Managed", "yes" if preview.managed else "no")
    if preview.host is not None:
        write_detail_line(
            out, "Connect", f"{preview.user}@{preview.host} port {preview.port}"
        )
    if preview.host_key is not None:
        write_detail_line(
            out,
            "Host key",
            f"{preview.host_key.key_type} {preview.host_key.fingerprint}",
        )
    write_detail_line(out, "Paths", ", ".join(preview.paths) or "-")
    write_detail_line(out, "Destination", preview.destination or "-")
    if preview.total_bytes is not None:
        write_detail_line(out, "Size", f"{preview.total_bytes} bytes")
    if preview.free_bytes is not None:
        write_detail_line(out, "Free", f"{preview.free_bytes} bytes")
    if preview.required_bytes is not None:
        write_detail_line(
            out, "Required", f"{preview.required_bytes} bytes (data plus margin)"
        )


def write_failure(
    services: CliServices,
    verb: str,
    outcome: TransferOutcome,
    reason: str,
    configuration: str,
    identifier: str,
    host_key: HostKey | None,
) -> int:
    """Print an outcome in which nothing was copied or verified; return 1."""
    err = services.stderr
    write_line(err, f"error: {verb} of server {identifier!r} failed: {reason}")
    write_line(err, f"Hint: {_hint(outcome, configuration, identifier, host_key)}")
    return exit_codes.FAILURE


def _hint(
    outcome: TransferOutcome,
    configuration: str,
    identifier: str,
    host_key: HostKey | None,
) -> str:
    if outcome is TransferOutcome.HOST_KEY_UNKNOWN and host_key is not None:
        return (
            f"check the fingerprint {host_key.fingerprint} with the owner of "
            f"the server; if it is right, run the same command again with "
            f"{ACCEPT_HOST_KEY} {host_key.fingerprint}."
        )
    hints = {
        TransferOutcome.HOST_KEY_CHANGED: (
            "the key differs from your known_hosts and is never accepted here; "
            "if the server was rebuilt, correct known_hosts by hand "
            "(ssh-keygen -R <host>)."
        ),
        TransferOutcome.ACCESS_MISSING: (
            f"run 'iac-node-forge server access {configuration} {identifier} "
            "--user <user>' first."
        ),
        TransferOutcome.SERVER_NOT_FOUND: (
            f"list the servers with 'iac-node-forge servers --configuration "
            f"{configuration}'."
        ),
        TransferOutcome.SERVER_NOT_RUNNING: (
            f"start it with 'iac-node-forge server start {configuration} {identifier}'."
        ),
        TransferOutcome.CONNECTION_FAILED: (
            "check the address, the user and that your key or agent can log in "
            "without a prompt."
        ),
        TransferOutcome.NO_ROOM: "free space at the destination or choose another.",
        TransferOutcome.DESTINATION_NOT_EMPTY: (
            "choose a directory that does not exist or is empty."
        ),
        TransferOutcome.NOT_CONFIRMED: "nothing was written on the server.",
        TransferOutcome.IDENTIFIER_MISMATCH: f"type {identifier!r} exactly.",
    }
    return hints.get(outcome, "check the paths and try again.") + (
        " Nothing was copied."
        if outcome
        not in {TransferOutcome.NOT_CONFIRMED, TransferOutcome.IDENTIFIER_MISMATCH}
        else ""
    )


def write_report(services: CliServices, result: TransferResult) -> int:
    """Print the report of a finished transfer; return the exit code."""
    out = services.stdout
    report = result.report
    if report is not None:
        _write_report_lines(services, report)
    if result.outcome is TransferOutcome.COMPLETED:
        write_line(out, "Copied and verified: the copy equals its source.")
        if result.reason:
            write_line(services.stderr, f"warning: {result.reason}")
        return exit_codes.SUCCESS
    err = services.stderr
    label = (
        "verification failed"
        if result.outcome is TransferOutcome.VERIFICATION_FAILED
        else "the transfer failed"
    )
    write_line(err, f"error: {label}: {result.reason}")
    write_line(err, "Hint: the copy is not verified; see the report.")
    return exit_codes.FAILURE


def _write_report_lines(services: CliServices, report: TransferReport) -> None:
    out = services.stdout
    write_detail_line(out, "Direction", report.direction.value)
    write_detail_line(out, "Files", str(report.file_count))
    write_detail_line(out, "Links", str(report.link_count))
    write_detail_line(out, "Bytes", str(report.byte_count))
    write_detail_line(out, "Verified", "yes" if report.verified else "no")
    for skipped in report.skipped[:_MAX_LISTED]:
        write_detail_line(out, "Skipped", f"{skipped.path} ({skipped.reason})")
    for replaced in report.overwritten[:_MAX_LISTED]:
        write_detail_line(out, "Replaced", replaced.path)
    for difference in report.differences[:_MAX_LISTED]:
        write_detail_line(
            out, "Difference", f"{difference.path} ({difference.kind.value})"
        )
    if report.note:
        write_detail_line(out, "Note", report.note)
    if report.report_file:
        write_detail_line(out, "Report", report.report_file)


def preview_failure(
    services: CliServices,
    verb: str,
    preview: TransferPreview,
    configuration: str,
) -> int:
    """Print the preview and the reason a transfer cannot be made."""
    write_preview(services, preview)
    return write_failure(
        services,
        verb,
        preview.outcome,
        preview.reason,
        configuration,
        preview.server_identifier,
        preview.host_key,
    )
