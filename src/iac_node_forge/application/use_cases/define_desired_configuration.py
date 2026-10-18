"""UC-003: define, re-check and list desired server configurations (ADR-0007)."""

from collections.abc import Mapping, Sequence

from iac_node_forge.application.capabilities import PROVISION_SERVER
from iac_node_forge.application.dto import DesiredConfigurationView
from iac_node_forge.application.errors import DesiredConfigurationNotFoundError
from iac_node_forge.application.ports import (
    ConfigurationStore,
    DesiredConfigurationStore,
)
from iac_node_forge.application.use_cases._configuration_lookup import (
    find_configuration,
)
from iac_node_forge.application.use_cases.desired_configuration_validator import (
    DesiredConfigurationValidator,
)
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.errors import DomainError
from iac_node_forge.domain.provider_configuration import ProviderConfiguration
from iac_node_forge.domain.provider_value import ProviderValue

_UNSUPPORTED = (
    f"the provider does not support provisioning (capability {PROVISION_SERVER})"
)


class DefineDesiredConfiguration:
    """Checks and saves named desired configurations.

    Nothing that fails the check is saved, and the only reads the provider
    sees are its capabilities, rules and options.
    """

    def __init__(
        self,
        validator: DesiredConfigurationValidator,
        configurations: ConfigurationStore,
        desired_configurations: DesiredConfigurationStore,
    ) -> None:
        self._validator = validator
        self._configurations = configurations
        self._desired = desired_configurations

    def define_desired_configuration(
        self,
        configuration: str,
        name: str,
        image: str,
        access_key_names: Sequence[str],
        bootstrap_hint: str | None,
        values: Mapping[str, str],
    ) -> DesiredConfigurationView:
        """Check the description and save it under ``name`` when it is valid.

        A description with the same name for the configuration is replaced. A
        description that is not valid is reported with each problem and is not
        saved. Raises the use case and provider errors of the reads
        (configuration not found, unreachable, rejected credentials).
        """
        target = find_configuration(self._configurations, configuration)
        try:
            desired = DesiredConfiguration(
                target.configuration_id,
                name,
                image,
                tuple(dict.fromkeys(access_key_names)),
                bootstrap_hint,
                tuple(ProviderValue(n, v) for n, v in values.items()),
            )
        except DomainError as error:
            return _unchecked_view(target, name, image, access_key_names, error)
        view = self._check(target, desired)
        if view.valid:
            self._desired.save(desired)
        return view

    def validate_desired_configuration(
        self, configuration: str, name: str
    ) -> DesiredConfigurationView:
        """Check a saved description again against what the provider offers now.

        Nothing is changed. Raises DesiredConfigurationNotFoundError when none
        has the name, and the errors of the reads.
        """
        target = find_configuration(self._configurations, configuration)
        desired = self._desired.load(target.configuration_id, name)
        if desired is None:
            raise DesiredConfigurationNotFoundError(
                f"no desired configuration {name!r} for {target.display_name!r}"
            )
        return self._check(target, desired)

    def list_desired_configurations(
        self, configuration: str, check: bool = False
    ) -> tuple[DesiredConfigurationView, ...]:
        """Return the saved descriptions of one configuration.

        Without ``check`` they are shown as they were saved, valid; with it
        each is checked against what the provider offers now.
        """
        target = find_configuration(self._configurations, configuration)
        saved = self._desired.load_all(target.configuration_id)
        if check:
            return tuple(self._check(target, desired) for desired in saved)
        return tuple(_view(desired, ()) for desired in saved)

    def _check(
        self, target: ProviderConfiguration, desired: DesiredConfiguration
    ) -> DesiredConfigurationView:
        if not self._validator.supports_provisioning(target):
            return _view(desired, (_UNSUPPORTED,))
        return _view(desired, tuple(self._validator.check(target, desired)))


def _view(
    desired: DesiredConfiguration, problems: tuple[str, ...]
) -> DesiredConfigurationView:
    return DesiredConfigurationView(
        desired.configuration_id,
        desired.desired_name,
        desired.image,
        desired.access_key_names,
        desired.bootstrap_hint,
        desired.values,
        valid=not problems,
        problems=problems,
    )


def _unchecked_view(
    target: ProviderConfiguration,
    name: str,
    image: str,
    access_key_names: Sequence[str],
    error: DomainError,
) -> DesiredConfigurationView:
    """The answer for a description the domain itself refuses to build."""
    return DesiredConfigurationView(
        target.configuration_id,
        name,
        image,
        tuple(access_key_names),
        None,
        (),
        valid=False,
        problems=(str(error),),
    )
