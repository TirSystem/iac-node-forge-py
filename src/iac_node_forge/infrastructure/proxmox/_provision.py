"""What a Proxmox VE creation is made of: its value rules and its request.

Pure functions, no network. The provider sends the request this module builds;
the two create paths here, ``/nodes/{node}/lxc`` and ``/nodes/{node}/qemu``,
are the only creations the adapter can send (ADR-0007).

A ``storage`` or ``template`` value is the option value the read side offers:
``<node>/<storage>`` and ``<node>/<volume id>``. Storage is read per node, so
a value names its node, and it must be the node of the ``node`` value.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote

from iac_node_forge.application.errors import ProviderError
from iac_node_forge.domain.desired_configuration import DesiredConfiguration
from iac_node_forge.domain.value_rule import ValueRule
from iac_node_forge.infrastructure import _power

KIND_LXC = "lxc"
KIND_QEMU = "qemu"
VALUE_KIND = "kind"
VALUE_NODE = "node"
VALUE_STORAGE = "storage"
VALUE_TEMPLATE = "template"
VALUE_MEMORY_MB = "memory_mb"
VALUE_CORES = "cores"
VALUE_DISK_GB = "disk_gb"
PERMISSION = "VM.Allocate"
"""The permission a creation needs (P13); named in a rejection, never a secret."""

MIN_MEMORY_MB = 16
MAX_MEMORY_MB = 4 * 1024 * 1024
MIN_CORES = 1
MAX_CORES = 512
MIN_DISK_GB = 1
MAX_DISK_GB = 64 * 1024
# A container or machine is created stopped and with a DHCP address on the
# default bridge; nothing about its network is part of the description yet.
_BRIDGE = "vmbr0"
_NOT_STARTED = "0"


@dataclass(frozen=True, slots=True)
class CreateRequest:
    """The path and the form fields of one create request."""

    path: str
    form: Mapping[str, str]


def value_rules() -> tuple[ValueRule, ...]:
    """Return the values a Proxmox VE creation needs, all required."""
    return (
        ValueRule(VALUE_KIND, required=True, allowed=(KIND_LXC, KIND_QEMU)),
        ValueRule(VALUE_NODE, required=True, option_kind="node"),
        ValueRule(VALUE_STORAGE, required=True, option_kind="storage"),
        ValueRule(VALUE_TEMPLATE, required=True, option_kind="template"),
        ValueRule(
            VALUE_MEMORY_MB,
            required=True,
            minimum=MIN_MEMORY_MB,
            maximum=MAX_MEMORY_MB,
        ),
        ValueRule(VALUE_CORES, required=True, minimum=MIN_CORES, maximum=MAX_CORES),
        ValueRule(
            VALUE_DISK_GB, required=True, minimum=MIN_DISK_GB, maximum=MAX_DISK_GB
        ),
    )


def build_request(
    desired: DesiredConfiguration, server_name: str, vmid: str
) -> CreateRequest:
    """Return the container or virtual machine creation for ``desired``.

    Raises ProviderError, naming the value, when one is missing or does not
    belong to the node; nothing has been sent at that point.
    """
    kind = _power.required_value(desired, VALUE_KIND)
    node = _power.required_value(desired, VALUE_NODE)
    storage = _on_node(
        node, _power.required_value(desired, VALUE_STORAGE), VALUE_STORAGE
    )
    template = _on_node(
        node, _power.required_value(desired, VALUE_TEMPLATE), VALUE_TEMPLATE
    )
    memory = _power.required_value(desired, VALUE_MEMORY_MB)
    cores = _power.required_value(desired, VALUE_CORES)
    disk = _power.required_value(desired, VALUE_DISK_GB)
    path = f"/nodes/{quote(node, safe='')}/{kind}"
    if kind == KIND_LXC:
        form = {
            "vmid": vmid,
            "hostname": server_name,
            "ostemplate": template,
            "rootfs": f"{storage}:{disk}",
            "memory": memory,
            "cores": cores,
            "unprivileged": "1",
            "net0": f"name=eth0,bridge={_BRIDGE},ip=dhcp",
            "start": _NOT_STARTED,
        }
    elif kind == KIND_QEMU:
        form = {
            "vmid": vmid,
            "name": server_name,
            "memory": memory,
            "cores": cores,
            "scsi0": f"{storage}:{disk}",
            "ide2": f"{template},media=cdrom",
            "net0": f"virtio,bridge={_BRIDGE}",
            "start": _NOT_STARTED,
        }
    else:
        raise ProviderError(f"the value {VALUE_KIND!r} must be lxc or qemu")
    return CreateRequest(path, form)


def _on_node(node: str, option_value: str, name: str) -> str:
    owner, separator, rest = option_value.partition("/")
    if not separator or not rest or owner != node:
        raise ProviderError(
            f"the value {name!r} must be of the form <node>/<name> on node {node!r}"
        )
    return rest
