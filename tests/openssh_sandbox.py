"""An OpenSSH configuration that cannot reach the user's real ``~/.ssh``.

The tests have a real key pair and a real known_hosts in ``~/.ssh`` on the
machine of the developer. A test that runs ssh builds its commands with
``isolated_commands``: its own empty ssh config, its own known_hosts files, no
agent, and only the throw-away key it names. ``test_openssh_isolation`` checks
this configuration.
"""

from pathlib import Path

from iac_node_forge.infrastructure.openssh import OpenSshCommands


def isolated_commands(directory: Path, *, connect_timeout: int = 10) -> OpenSshCommands:
    """Return commands that use files in ``directory`` and nothing of the user's."""
    config = directory / "ssh_config"
    config.write_text("", encoding="utf-8", newline="\n")
    global_hosts = directory / "global_known_hosts"
    global_hosts.write_text("", encoding="utf-8", newline="\n")
    return OpenSshCommands(
        known_hosts_file=directory / "known_hosts",
        config_file=config,
        extra_options=(
            f"GlobalKnownHostsFile={global_hosts}",
            "IdentityAgent=none",
            "IdentitiesOnly=yes",
            "PreferredAuthentications=publickey",
        ),
        connect_timeout=connect_timeout,
    )
