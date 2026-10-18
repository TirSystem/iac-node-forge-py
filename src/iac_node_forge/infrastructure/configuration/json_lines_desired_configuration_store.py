"""DesiredConfigurationStore kept in the client's JSON Lines file (ERD-001).

The desired configurations live in the same file as the configuration, as
``desired_configuration`` lines with child lines for the access key names and
the values; every other record is written back unchanged. Only the names of
access keys are stored, never a key value.
"""

from pathlib import Path

from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.infrastructure.configuration._json_lines_file import (
    read_snapshot,
    write_snapshot,
)


class JsonLinesDesiredConfigurationStore:
    """Keeps desired configurations as records of the client's JSON Lines file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, desired: DesiredConfiguration) -> None:
        """Insert the desired configuration, or replace the one with its key."""
        snapshot = read_snapshot(self._path)
        snapshot.desired[desired.configuration_id, desired.desired_name] = desired
        write_snapshot(self._path, snapshot)

    def load(
        self, configuration_id: ConfigurationId, desired_name: str
    ) -> DesiredConfiguration | None:
        """Return the desired configuration with this key, or None."""
        return read_snapshot(self._path).desired.get((configuration_id, desired_name))

    def load_all(
        self, configuration_id: ConfigurationId
    ) -> tuple[DesiredConfiguration, ...]:
        """Return the desired configurations of one provider configuration."""
        return tuple(
            desired
            for (owner, _), desired in read_snapshot(self._path).desired.items()
            if owner == configuration_id
        )
