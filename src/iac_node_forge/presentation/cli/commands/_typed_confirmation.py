"""The typed confirmation of an action that cannot be undone, at the command line.

A removal and a restore ask for the same thing: the server identifier typed, and
typed once more for a server this client does not manage.
"""

from iac_node_forge.application.dto import Confirmation
from iac_node_forge.presentation.cli.output import write_line
from iac_node_forge.presentation.cli.services import CliServices


def typed_confirmation(
    services: CliServices,
    verb: str,
    identifier: str,
    managed: bool,
    yes: bool,
    confirm_unmanaged: str | None,
) -> Confirmation:
    """Return what the user confirmed for ``verb`` (such as ``remove``).

    Without a terminal nothing is asked: ``yes`` is the confirmation, the
    identifier on the command line counts as typed and ``confirm_unmanaged`` as
    the identifier typed again. At a terminal the identifier is always asked for,
    whatever ``yes`` says.
    """
    if not services.is_interactive:
        return Confirmation(
            given=yes,
            typed_identifier=identifier,
            unmanaged_identifier=confirm_unmanaged,
        )
    typed = services.read_line(
        f"Type the server identifier {identifier!r} to {verb} the server: "
    ).strip()
    if not typed:
        return Confirmation(given=False)
    again = confirm_unmanaged
    if not managed and again is None and typed == identifier:
        again = services.read_line(
            "This server is not managed by this client; type its identifier "
            "again to confirm: "
        ).strip()
    return Confirmation(
        given=True, typed_identifier=typed, unmanaged_identifier=again or None
    )


def write_not_confirmed(
    services: CliServices, noun: str, identifier: str, managed: bool, interactive: bool
) -> None:
    """Say that nothing was sent because the confirmation was not given."""
    err = services.stderr
    if interactive:
        write_line(err, f"error: the {noun} was not confirmed and was cancelled")
        write_line(err, "Hint: no request was sent.")
        return
    write_line(
        err,
        f"error: the {noun} of server {identifier!r} needs confirmation and no "
        "terminal is available to ask",
    )
    needed = "--yes"
    if not managed:
        needed += f" --confirm-unmanaged {identifier}"
    write_line(err, f"Hint: repeat the command with {needed}. No request was sent.")
