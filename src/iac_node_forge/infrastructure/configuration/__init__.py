"""Configuration storage adapters."""

from iac_node_forge.infrastructure.configuration.json_lines_store import (
    JsonLinesConfigurationStore,
    default_configuration_path,
)

__all__ = ["JsonLinesConfigurationStore", "default_configuration_path"]
