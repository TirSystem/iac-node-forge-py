"""The only place that builds the command lines of the OpenSSH tools (ADR-0009).

Local commands are lists of arguments and are never run through a shell. A
remote command is one string for the shell of the server; it is made from the
fixed templates below, and every path in it is quoted for a POSIX shell and
follows ``--`` where the program accepts it. No secret is ever placed on a
command line: a password or passphrase prompt is left to ``ssh``.
"""

import shlex
from collections.abc import Sequence
from pathlib import Path

from iac_node_forge.domain.errors import InvalidRemotePathError
from iac_node_forge.domain.remote_path import (
    ROOT,
    validate_relative_path,
    validate_remote_path,
)
from iac_node_forge.domain.server_access import DEFAULT_PORT, RemoteAccess

READ_PROGRAMS = frozenset({"tar", "find", "sha256sum", "stat", "readlink", "du"})
"""The programs a backup may run on the server; ``tar`` only to create or list."""
WRITE_PROGRAMS = frozenset({"tar", "mkdir"})
"""The programs a restore may add: ``tar -xf -`` and ``mkdir -p``."""
ALLOWED_REMOTE_PROGRAMS = READ_PROGRAMS | WRITE_PROGRAMS

_KEY_TYPES = "ed25519,ecdsa,rsa"
_SERVER_ALIVE_SECONDS = 15
_SERVER_ALIVE_COUNT = 4


def host_spec(host: str, port: int) -> str:
    """Return how ``known_hosts`` names the host: ``host`` or ``[host]:port``."""
    return host if port == DEFAULT_PORT else f"[{host}]:{port}"


class OpenSshCommands:
    """Builds the argument lists of ssh, ssh-keyscan and ssh-keygen.

    ``known_hosts_file`` and ``config_file`` point ssh at files other than the
    user's own, so a test never reads or writes the real ``~/.ssh``;
    ``extra_options`` are added as ``-o`` options (``"Name=value"``). With none
    of them, ssh uses the user's agent, key files, ``ssh_config`` and
    ``known_hosts``. ``interactive`` lets ssh ask for a password or a
    passphrase on the terminal; otherwise it runs with ``BatchMode=yes`` and
    fails instead of waiting.
    """

    def __init__(
        self,
        *,
        ssh_executable: str = "ssh",
        keyscan_executable: str = "ssh-keyscan",
        keygen_executable: str = "ssh-keygen",
        known_hosts_file: Path | None = None,
        config_file: Path | None = None,
        extra_options: Sequence[str] = (),
        interactive: bool = False,
        connect_timeout: int = 15,
    ) -> None:
        for option in extra_options:
            if "=" not in option or option.startswith("-"):
                raise ValueError("an extra option is written Name=value")
        self._ssh = ssh_executable
        self._keyscan = keyscan_executable
        self._keygen = keygen_executable
        self._known_hosts_file = known_hosts_file
        self._config_file = config_file
        self._extra_options = tuple(extra_options)
        self._interactive = interactive
        self._connect_timeout = connect_timeout

    @property
    def known_hosts_file(self) -> Path | None:
        """Return the known_hosts file in use, or None for the user's own."""
        return self._known_hosts_file

    @property
    def connect_timeout(self) -> int:
        """Return the seconds ssh waits for a connection."""
        return self._connect_timeout

    @property
    def is_isolated(self) -> bool:
        """Return True when neither the user's known_hosts nor ssh_config is used."""
        return self._known_hosts_file is not None and self._config_file is not None

    # Local commands -------------------------------------------------------

    def ssh(self, access: RemoteAccess, remote_command: str) -> list[str]:
        """Return the ssh command that runs ``remote_command`` on the server."""
        argv = [self._ssh, "-T", *self._options()]
        argv += ["-p", str(access.port), "-l", access.user]
        argv += self._identity(access.identity_hint)
        return [*argv, "--", access.host, remote_command]

    def keyscan(self, host: str, port: int) -> list[str]:
        """Return the ssh-keyscan command that reads the host keys."""
        return [
            self._keyscan,
            "-T",
            str(self._connect_timeout),
            "-p",
            str(port),
            "-t",
            _KEY_TYPES,
            "--",
            host,
        ]

    def fingerprints(self) -> list[str]:
        """Return the ssh-keygen command that prints the SHA-256 fingerprint of
        each key line given on its standard input."""
        return [self._keygen, "-l", "-E", "sha256", "-f", "-"]

    def known_entries(self, host: str, port: int) -> list[str]:
        """Return the ssh-keygen command that finds the entries of a host."""
        argv = [self._keygen, "-F", host_spec(host, port)]
        if self._known_hosts_file is not None:
            argv += ["-f", str(self._known_hosts_file)]
        return argv

    def _options(self) -> list[str]:
        options = [
            "StrictHostKeyChecking=yes",
            f"ConnectTimeout={self._connect_timeout}",
            f"ServerAliveInterval={_SERVER_ALIVE_SECONDS}",
            f"ServerAliveCountMax={_SERVER_ALIVE_COUNT}",
            "ForwardAgent=no",
            "ForwardX11=no",
            "ClearAllForwardings=yes",
        ]
        if not self._interactive:
            options.append("BatchMode=yes")
        if self._known_hosts_file is not None:
            options.append(f"UserKnownHostsFile={self._known_hosts_file}")
        options += self._extra_options
        argv = [part for option in options for part in ("-o", option)]
        if self._config_file is not None:
            argv += ["-F", str(self._config_file)]
        return argv

    @staticmethod
    def _identity(hint: str | None) -> list[str]:
        if hint is None:
            return []
        try:
            is_file = Path(hint).expanduser().is_file()
        except (OSError, ValueError):
            return []
        if not is_file:
            return []
        return ["-i", str(Path(hint).expanduser()), "-o", "IdentitiesOnly=yes"]

    # Remote commands: reads -------------------------------------------------

    @staticmethod
    def tar_create(remote_paths: Sequence[str]) -> str:
        """Return ``tar -cf - -C / -- <names>`` for absolute ``remote_paths``."""
        return "tar -cf - -C / -- " + _words(tar_names(remote_paths))

    @staticmethod
    def tar_owner_probe() -> str:
        """Return the read that tells whether ``tar`` knows ``--no-same-owner``.

        It lists an empty archive read from its standard input, so it fails only
        for an option it does not know.
        """
        return "tar --no-same-owner -tf -"

    @staticmethod
    def disk_usage(paths: Sequence[str]) -> str:
        """Return ``du -sk -- <paths>`` for absolute ``paths``."""
        return "du -sk -- " + _words(_absolute(paths))

    @staticmethod
    def find_hashes(paths: Sequence[str]) -> str:
        """Return the find that hashes every regular file under ``paths``."""
        return f"find {_words(_absolute(paths))} -type f -exec sha256sum -- {{}} +"

    @staticmethod
    def find_sizes(paths: Sequence[str]) -> str:
        """Return the find that prints size and name of every regular file."""
        return (
            f"find {_words(_absolute(paths))} -type f "
            f"-exec stat -c {shlex.quote('%s %n')} -- {{}} +"
        )

    @staticmethod
    def find_links(paths: Sequence[str]) -> str:
        """Return the find that prints each link, then its target, line by line."""
        return (
            f"find {_words(_absolute(paths))} -type l -print "
            f"-exec readlink -- {{}} {shlex.quote(';')}"
        )

    @staticmethod
    def stat_paths(paths: Sequence[str]) -> str:
        """Return ``stat`` printing size, modification time and name of ``paths``."""
        return f"stat -c {shlex.quote('%s %Y %n')} -- " + _words(_absolute(paths))

    # Remote commands: writes (a restore only) -------------------------------

    @staticmethod
    def make_directory_and_extract(destination: str, no_same_owner: bool) -> str:
        """Return ``mkdir -p`` and ``tar -xf -`` into the absolute ``destination``."""
        quoted = shlex.quote(validate_remote_path("destination", destination))
        owner = "--no-same-owner " if no_same_owner else ""
        return f"mkdir -p -- {quoted} && tar -xf - {owner}-C {quoted}"


def tar_names(remote_paths: Sequence[str]) -> list[str]:
    """Return the names ``tar -C /`` takes for absolute paths: no leading slash.

    The root itself is refused: backing up ``/`` is not offered.
    """
    names = [path[len(ROOT) :] for path in _absolute(remote_paths)]
    if "" in names:
        raise InvalidRemotePathError("the root '/' cannot be backed up")
    return [validate_relative_path("remote path", name) for name in names]


def _absolute(paths: Sequence[str]) -> list[str]:
    if not paths:
        raise InvalidRemotePathError("no path given")
    return [validate_remote_path("remote path", path) for path in paths]


def _words(paths: Sequence[str]) -> str:
    return " ".join(shlex.quote(path) for path in paths)
