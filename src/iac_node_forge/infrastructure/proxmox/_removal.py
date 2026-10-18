"""What a Proxmox VE removal is made of: its request, its consequence and its block.

Pure functions, no network. The provider sends the one request this module
describes, ``DELETE /nodes/{node}/lxc/{vmid}`` or ``.../qemu/{vmid}`` with the
options ``purge`` and ``destroy-unreferenced-disks`` (ADR-0008, P10). The
deletion happens at once; a running machine is refused by the caller and never
stopped on the user's behalf.
"""

from urllib.parse import quote

from iac_node_forge.application.ports.server_removal_port import RemovalBlock
from iac_node_forge.domain.server import Server

PERMISSION = "VM.Allocate"
"""The permission a deletion needs (P13); named in a rejection, never a secret."""

_OPTIONS = "purge=1&destroy-unreferenced-disks=1"
_RUNNING = "running"


def delete_path(node: str, guest_type: str, vmid: str) -> str:
    """Return the path, with its options, of the deletion of one guest."""
    return f"/nodes/{quote(node, safe='')}/{guest_type}/{vmid}?{_OPTIONS}"


def consequence(server: Server) -> str:
    """State what a removal does: the server is destroyed now, for good."""
    return (
        f"This destroys server {server.server_identifier} now: the machine and "
        "its disks are deleted at Proxmox VE, and this cannot be undone."
    )


def block(server: Server) -> RemovalBlock | None:
    """Return RUNNING for a running server, which must be shut down first.

    A destroyed server is absent from the provider, so ALREADY_REMOVED does not
    apply here: reading it raises ServerNotFoundError instead.
    """
    return RemovalBlock.RUNNING if server.state == _RUNNING else None
