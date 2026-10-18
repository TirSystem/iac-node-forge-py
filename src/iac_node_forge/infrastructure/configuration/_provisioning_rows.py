"""The records of desired configurations and provision intents (ERD-001).

One line per entity, child entities on lines of their own: a desired
configuration has one ``desired_configuration`` line, one ``access_key_name``
line per key name and one ``desired_configuration_value`` line per value; an
intent has one ``provision_intent`` line and one ``provision_intent_value`` line
per value. The order of the child lines is the order of the lists. No record
has a field that can hold a secret: a key line holds a key name only.
"""

from datetime import datetime

from iac_node_forge.application.errors import ConfigurationFormatError
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.operation import OperationId
from iac_node_forge.domain.provider_configuration import ConfigurationId
from iac_node_forge.domain.provider_value import ProviderValue
from iac_node_forge.domain.provision_intent import (
    IntentId,
    IntentState,
    ProvisionIntent,
)
from iac_node_forge.infrastructure.configuration._rows import Row, optional_text, text

DESIRED = "desired_configuration"
KEY_NAME = "access_key_name"
DESIRED_VALUE = "desired_configuration_value"
INTENT = "provision_intent"
INTENT_VALUE = "provision_intent_value"

FIELDS: dict[str, frozenset[str]] = {
    DESIRED: frozenset(
        {"record_type", "configuration_id", "desired_name", "image", "bootstrap_hint"}
    ),
    KEY_NAME: frozenset(
        {"record_type", "configuration_id", "desired_name", "key_name"}
    ),
    DESIRED_VALUE: frozenset(
        {"record_type", "configuration_id", "desired_name", "value_name", "value"}
    ),
    INTENT: frozenset(
        {
            "record_type",
            "intent_id",
            "configuration_id",
            "desired_name",
            "server_name",
            "image",
            "reserved_identifier",
            "state",
            "recorded_at",
            "operation_id",
        }
    ),
    INTENT_VALUE: frozenset({"record_type", "intent_id", "value_name", "value"}),
}
NULLABLE = frozenset({"bootstrap_hint", "reserved_identifier", "operation_id"})

type DesiredKey = tuple[ConfigurationId, str]


def desired_rows(desired: DesiredConfiguration) -> list[Row]:
    """Return the lines of a desired configuration and its children."""
    key: Row = {
        "configuration_id": desired.configuration_id,
        "desired_name": desired.desired_name,
    }
    rows: list[Row] = [
        {
            "record_type": DESIRED,
            **key,
            "image": desired.image,
            "bootstrap_hint": desired.bootstrap_hint,
        }
    ]
    rows += [
        {"record_type": KEY_NAME, **key, "key_name": name}
        for name in desired.access_key_names
    ]
    rows += [
        {
            "record_type": DESIRED_VALUE,
            **key,
            "value_name": value.name,
            "value": value.value,
        }
        for value in desired.values
    ]
    return rows


def intent_rows(intent: ProvisionIntent) -> list[Row]:
    """Return the lines of an intent and its copied values."""
    rows: list[Row] = [
        {
            "record_type": INTENT,
            "intent_id": intent.intent_id,
            "configuration_id": intent.configuration_id,
            "desired_name": intent.desired_name,
            "server_name": intent.server_name,
            "image": intent.image,
            "reserved_identifier": intent.reserved_identifier,
            "state": intent.state.value,
            "recorded_at": intent.recorded_at.isoformat(),
            "operation_id": intent.operation_id,
        }
    ]
    rows += [
        {
            "record_type": INTENT_VALUE,
            "intent_id": intent.intent_id,
            "value_name": value.name,
            "value": value.value,
        }
        for value in intent.values
    ]
    return rows


class ProvisioningRecords:
    """Collects the lines of both entities and builds them once all are read."""

    def __init__(self) -> None:
        self._desired: dict[DesiredKey, Row] = {}
        self._keys: dict[DesiredKey, list[str]] = {}
        self._values: dict[DesiredKey, list[ProviderValue]] = {}
        self._intents: dict[IntentId, Row] = {}
        self._intent_values: dict[IntentId, list[ProviderValue]] = {}

    def add(self, row: Row) -> bool:
        """Take a line of these entities; return False for any other line."""
        kind = row["record_type"]
        if kind == DESIRED:
            self._desired[_desired_key(row)] = row
        elif kind == KEY_NAME:
            self._keys.setdefault(_desired_key(row), []).append(text(row, "key_name"))
        elif kind == DESIRED_VALUE:
            self._values.setdefault(_desired_key(row), []).append(_value(row))
        elif kind == INTENT:
            self._intents[IntentId(text(row, "intent_id"))] = row
        elif kind == INTENT_VALUE:
            intent_id = IntentId(text(row, "intent_id"))
            self._intent_values.setdefault(intent_id, []).append(_value(row))
        else:
            return False
        return True

    def desired_configurations(self) -> dict[DesiredKey, DesiredConfiguration]:
        """Return the desired configurations; a child line needs its parent."""
        for children in (self._keys, self._values):
            if not children.keys() <= self._desired.keys():
                raise ConfigurationFormatError("a line names an unknown description")
        return {
            key: DesiredConfiguration(
                key[0],
                key[1],
                text(row, "image"),
                tuple(self._keys.get(key, ())),
                optional_text(row, "bootstrap_hint"),
                tuple(self._values.get(key, ())),
            )
            for key, row in self._desired.items()
        }

    def intents(self) -> dict[IntentId, ProvisionIntent]:
        """Return the intents; a value line needs its intent."""
        if not self._intent_values.keys() <= self._intents.keys():
            raise ConfigurationFormatError("a line names an unknown intent")
        return {
            intent_id: _intent(row, tuple(self._intent_values.get(intent_id, ())))
            for intent_id, row in self._intents.items()
        }


def _intent(row: Row, values: tuple[ProviderValue, ...]) -> ProvisionIntent:
    operation_id = optional_text(row, "operation_id")
    return ProvisionIntent(
        intent_id=IntentId(text(row, "intent_id")),
        configuration_id=ConfigurationId(text(row, "configuration_id")),
        desired_name=text(row, "desired_name"),
        server_name=text(row, "server_name"),
        image=text(row, "image"),
        values=values,
        state=IntentState(text(row, "state")),
        recorded_at=datetime.fromisoformat(text(row, "recorded_at")),
        reserved_identifier=optional_text(row, "reserved_identifier"),
        operation_id=OperationId(operation_id) if operation_id else None,
    )


def _desired_key(row: Row) -> DesiredKey:
    return ConfigurationId(text(row, "configuration_id")), text(row, "desired_name")


def _value(row: Row) -> ProviderValue:
    return ProviderValue(text(row, "value_name"), text(row, "value"))
