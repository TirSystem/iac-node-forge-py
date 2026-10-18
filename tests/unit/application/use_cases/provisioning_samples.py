"""Samples shared by the provisioning use case tests: one provider with rules."""

from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.provider_value import ProviderValue
from iac_node_forge.domain.server import Server
from iac_node_forge.domain.server_option import ServerOption
from iac_node_forge.domain.value_rule import ValueRule
from tests.unit.application.fakes import FakeProvisionProvider
from tests.unit.application.samples import LAB_ID

RULES = (
    ValueRule("template", required=True, option_kind="template"),
    ValueRule("node", required=True, option_kind="node"),
    ValueRule("kind", required=True, allowed=("lxc", "qemu")),
    ValueRule("cores", required=True, minimum=1, maximum=8),
    ValueRule("pool", required=False, option_kind="pool"),
)
OPTIONS = (
    ServerOption("template", "pve1/local:vztmpl/alpine.tar.xz"),
    ServerOption("template", "pve1/local:vztmpl/debian.tar.zst"),
    ServerOption("node", "pve1"),
    ServerOption("node", "pve2"),
    ServerOption("pool", "dev"),
)
IMAGE = "pve1/local:vztmpl/alpine.tar.xz"
GOOD_VALUES = (
    ProviderValue("node", "pve1"),
    ProviderValue("kind", "lxc"),
    ProviderValue("cores", "2"),
)
WEB_SERVER = Server("100", "web", "running", "1 vCPU", (), False)


def desired(
    name: str = "small",
    image: str = IMAGE,
    values: tuple[ProviderValue, ...] = GOOD_VALUES,
) -> DesiredConfiguration:
    """A valid description for the fake provider's rules and options."""
    return DesiredConfiguration(LAB_ID, name, image, ("deploy",), "boot", values)


def provider(
    *,
    steps_to_finish: int = 2,
    cost: str | None = None,
    reserves: bool = True,
    supported: bool = True,
) -> FakeProvisionProvider:
    """The fake provider with the sample rules, options and one server, WEB."""
    return FakeProvisionProvider(
        servers=(WEB_SERVER,),
        options=OPTIONS,
        rules=RULES,
        supported=supported,
        reserves=reserves,
        cost=cost,
        steps_to_finish=steps_to_finish,
    )
