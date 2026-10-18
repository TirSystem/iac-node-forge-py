"""The kinds of action an operation records: four power actions and a provision."""

from enum import StrEnum


class PowerAction(StrEnum):
    """Names the action of an operation; the value is the word shown to a user.

    The type keeps its name from the power actions; PROVISION was added for the
    creation of a server (ADR-0007) and is not a power action. Code that offers
    or sends power requests uses ``POWER_ACTIONS``, never the whole enum.
    """

    START = "start"
    SHUTDOWN = "shutdown"
    STOP = "stop"
    RESTART = "restart"
    PROVISION = "provision"


POWER_ACTIONS: tuple[PowerAction, ...] = (
    PowerAction.START,
    PowerAction.SHUTDOWN,
    PowerAction.STOP,
    PowerAction.RESTART,
)
"""The four actions that change the power state of an existing server."""
