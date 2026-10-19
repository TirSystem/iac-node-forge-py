"""A throw-away SSH server in a Docker container for the transfer tests.

It is built from ``tests/integration/sshd`` (alpine and openssh-server only),
listens on 127.0.0.1:2222, and accepts one throw-away key pair that is made in a
temporary directory for the run. Nothing of the user's ``~/.ssh`` is read or
written. The tests that use it need ``IACNF_SSHD_TEST=1`` and a working Docker,
and are skipped with the reason otherwise.
"""

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

OPT_IN_VARIABLE = "IACNF_SSHD_TEST"
CONTAINER = "iacnf-sshd"
PROJECT = "iacnf-sshd"
HOST = "127.0.0.1"
PORT = 2222
USER = "tester"
REMOTE_ROOT = "/home/tester/work"
COMPOSE_FILE = Path(__file__).parent / "docker-compose.sshd.yml"
_READY_SECONDS = 60
_DOCKER_SECONDS = 600


@dataclass(frozen=True, slots=True)
class SshdServer:
    """How to reach the running container."""

    key: Path
    host: str = HOST
    port: int = PORT
    user: str = USER
    remote_root: str = REMOTE_ROOT


def unavailable_reason() -> str | None:
    """Return why the real-server tests cannot run here, or None when they can."""
    if os.environ.get(OPT_IN_VARIABLE) != "1":
        return f"set {OPT_IN_VARIABLE}=1 to run against a throw-away sshd container"
    for program in ("docker", "ssh", "ssh-keygen", "ssh-keyscan"):
        if shutil.which(program) is None:
            return f"{program} is not installed"
    try:
        info = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return "docker did not answer"
    if info.returncode != 0:
        return "the docker daemon is not running"
    return None


def make_key_pair(directory: Path, name: str = "id_throwaway") -> Path:
    """Make an ed25519 key pair without a passphrase; return the private file."""
    key = directory / name
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "iacnf-test",
            "-f",
            str(key),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return key


def public_key_line(key: Path) -> str:
    """Return the one line of the public half of ``key``."""
    return key.with_name(key.name + ".pub").read_text("utf-8").strip()


@contextmanager
def running_sshd(directory: Path) -> Iterator[SshdServer]:
    """Build and start the container with a new key pair; remove it afterwards."""
    key = make_key_pair(directory)
    environment = {**os.environ, "IACNF_SSHD_PUBLIC_KEY": public_key_line(key)}
    try:
        _compose(["up", "-d", "--build"], environment)
        _wait_for_ssh()
        yield SshdServer(key)
    finally:
        _compose(["down", "--remove-orphans", "--timeout", "2"], environment, False)


def exec_in_container(
    command: Sequence[str], stdin: bytes | None = None, user: str = USER
) -> subprocess.CompletedProcess[bytes]:
    """Run a command inside the container; it prepares and clears test data."""
    return subprocess.run(
        ["docker", "exec", "-i", "-u", user, CONTAINER, *command],
        input=stdin if stdin is not None else b"",
        capture_output=True,
        timeout=120,
        check=True,
    )


def _compose(
    arguments: list[str], environment: dict[str, str], check: bool = True
) -> None:
    subprocess.run(
        ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *arguments],
        env=environment,
        capture_output=True,
        timeout=_DOCKER_SECONDS,
        check=check,
    )


def _wait_for_ssh() -> None:
    deadline = time.monotonic() + _READY_SECONDS
    while time.monotonic() < deadline:
        if _banner_seen():
            return
        time.sleep(0.5)
    raise TimeoutError(f"sshd did not answer on {HOST}:{PORT} in {_READY_SECONDS} s")


def _banner_seen() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=2) as connection:
            connection.settimeout(2)
            return connection.recv(4).startswith(b"SSH-")
    except OSError:
        return False
