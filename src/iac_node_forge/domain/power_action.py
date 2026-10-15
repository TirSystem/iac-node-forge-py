"""The four power actions the product can request of a server."""

from enum import StrEnum


class PowerAction(StrEnum):
    """Names one power action; the value is the word shown to a user."""

    START = "start"
    SHUTDOWN = "shutdown"
    STOP = "stop"
    RESTART = "restart"
