"""Running the OpenSSH tools, with a timeout, and mapping their failures.

Every failure is mapped to a TransferError whose text is a fixed sentence or a
bounded, filtered line of what the tool said: a line that looks like it holds a
secret is withheld.
"""

import re
import subprocess
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import IO, Self

from iac_node_forge.application.errors import (
    ConnectionFailedError,
    HostKeyChangedError,
    HostKeyUnknownError,
    PermissionDeniedError,
    RemotePathNotFoundError,
    TransferError,
    TransferFailedError,
)

SSH_FAILURE = 255
"""The exit status ssh itself uses when it fails; anything else is the remote's."""
COMMAND_TIMEOUT = 120.0
"""Seconds a short command (a scan, a manifest) may run."""
STREAM_TIMEOUT = 4 * 60 * 60.0
"""Seconds a whole transfer may run; a dead connection ends sooner (keep-alive)."""

_MAX_SUMMARY = 300
_SECRET_LINE = re.compile(
    r"passw|passphrase|secret|token|credential|private key|begin .*key", re.IGNORECASE
)
_UNREACHABLE = (
    "connection refused",
    "timed out",
    "no route to host",
    "could not resolve",
    "name or service not known",
    "network is unreachable",
    "connection closed",
    "connection reset",
    "broken pipe",
)


@dataclass(frozen=True, slots=True)
class Completed:
    """What a finished command left."""

    returncode: int
    stdout: bytes
    stderr: str


def run(
    argv: Sequence[str], *, stdin: bytes | None = None, timeout: float = COMMAND_TIMEOUT
) -> Completed:
    """Run ``argv`` without a shell and wait for it, at most ``timeout`` seconds.

    Raises ConnectionFailedError when the program is missing or times out.
    """
    try:
        result = subprocess.run(
            list(argv),
            input=stdin if stdin is not None else b"",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise _missing(argv) from error
    except subprocess.TimeoutExpired as error:
        raise ConnectionFailedError(f"{_program(argv)} timed out") from error
    return Completed(
        result.returncode, result.stdout, result.stderr.decode("utf-8", "replace")
    )


def text(output: bytes) -> str:
    """Return the output as text; bytes that are not UTF-8 are kept reversibly."""
    return output.decode("utf-8", "surrogateescape")


class StreamedProcess:
    """A command whose standard input or output is a pipe the caller uses.

    The standard error goes to a temporary file, so a chatty command cannot fill
    a pipe and block. A timer kills the command after ``timeout`` seconds.
    """

    def __init__(
        self,
        argv: Sequence[str],
        *,
        feed: bool = False,
        capture: bool = False,
        timeout: float = STREAM_TIMEOUT,
    ) -> None:
        self._argv = list(argv)
        self._feed = feed
        self._capture = capture
        self._timeout = timeout
        self._timed_out = False
        self._stderr = tempfile.TemporaryFile()
        self._timer = threading.Timer(timeout, self._expire)
        self._process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> Self:
        try:
            self._process = subprocess.Popen(
                self._argv,
                stdin=subprocess.PIPE if self._feed else subprocess.DEVNULL,
                stdout=subprocess.PIPE if self._capture else subprocess.DEVNULL,
                stderr=self._stderr,
            )
        except FileNotFoundError as error:
            self._stderr.close()
            raise _missing(self._argv) from error
        self._timer.daemon = True
        self._timer.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._timer.cancel()
        self.abort()
        self._stderr.close()

    @property
    def stdin(self) -> IO[bytes]:
        """Return the pipe to the standard input of the command."""
        assert self._process is not None
        assert self._process.stdin is not None
        return self._process.stdin

    @property
    def stdout(self) -> IO[bytes]:
        """Return the pipe from the standard output of the command."""
        assert self._process is not None
        assert self._process.stdout is not None
        return self._process.stdout

    def is_running(self) -> bool:
        """Return True while the command has not ended."""
        return self._process is not None and self._process.poll() is None

    def wait_for_end(self, seconds: float) -> None:
        """Wait up to ``seconds`` for the command to end on its own."""
        assert self._process is not None
        try:
            self._process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            pass  # the caller asks is_running() and decides

    def abort(self) -> None:
        """Stop the command if it still runs."""
        process = self._process
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()

    def finish(self) -> Completed:
        """Close the pipes, wait for the command and return how it ended."""
        process = self._process
        assert process is not None
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass  # the command died first; its status explains
        if process.stdout is not None:
            while process.stdout.read(1 << 16):
                pass  # let a command that still writes padding end
        process.wait()
        self._stderr.seek(0)
        stderr = self._stderr.read().decode("utf-8", "replace")
        if self._timed_out:
            stderr = f"timed out after {self._timeout:.0f} seconds\n{stderr}"
        return Completed(process.returncode, b"", stderr)

    def _expire(self) -> None:
        self._timed_out = True
        process = self._process
        if process is not None and process.poll() is None:
            process.kill()


def summary(stderr: str) -> str:
    """Return a short, filtered extract of what a tool said on its error output."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    safe = [
        "[line withheld]" if _SECRET_LINE.search(line) else line for line in lines[:3]
    ]
    return " | ".join(safe)[:_MAX_SUMMARY] or "no reason given"


def failure(completed: Completed, what: str) -> TransferError:
    """Return the error that describes a command that did not succeed."""
    lowered = completed.stderr.lower()
    if lowered.startswith("timed out after"):
        return TransferFailedError(summary(completed.stderr))
    if "remote host identification has changed" in lowered:
        return HostKeyChangedError("the host key differs from the one in known_hosts")
    if "host key verification failed" in lowered:
        return HostKeyUnknownError("the host key is not accepted in known_hosts")
    if completed.returncode == SSH_FAILURE:
        return _ssh_failure(completed.stderr)
    if "no such file or directory" in lowered:
        return RemotePathNotFoundError(f"{what}: {summary(completed.stderr)}")
    if "permission denied" in lowered:
        return PermissionDeniedError(
            f"{what}: the user may not read or write a path "
            f"({summary(completed.stderr)})"
        )
    return TransferFailedError(
        f"{what} failed with status {completed.returncode}: {summary(completed.stderr)}"
    )


def _ssh_failure(stderr: str) -> TransferError:
    lowered = stderr.lower()
    if "permission denied" in lowered:
        return PermissionDeniedError("the server refused the log-in")
    if any(marker in lowered for marker in _UNREACHABLE):
        return ConnectionFailedError(summary(stderr))
    return ConnectionFailedError(f"ssh failed: {summary(stderr)}")


def require_success(completed: Completed, what: str) -> Completed:
    """Return ``completed`` when it ended with status 0, else raise its failure."""
    if completed.returncode != 0:
        raise failure(completed, what)
    return completed


def _missing(argv: Sequence[str]) -> ConnectionFailedError:
    return ConnectionFailedError(
        f"the OpenSSH program '{_program(argv)}' was not found on this computer"
    )


def _program(argv: Sequence[str]) -> str:
    return str(argv[0]) if argv else "program"
