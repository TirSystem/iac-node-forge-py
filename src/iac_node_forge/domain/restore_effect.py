"""What a restore does, as the port states it before anything is sent."""

from dataclasses import dataclass

from iac_node_forge.domain._validation import require_text
from iac_node_forge.domain.backup import Backup
from iac_node_forge.domain.errors import OperationTextTooLongError
from iac_node_forge.domain.operation import MAX_CONSEQUENCE_LENGTH


@dataclass(frozen=True, slots=True)
class RestoreEffect:
    """The consequence text of a restore and the backups it deletes.

    ``deleted_backups`` holds the snapshots newer than the chosen one for a
    SNAPSHOT and is empty for an ARCHIVE. The text is recorded as the
    consequence of the operation, so it fits that bound and holds no secret.
    """

    consequence: str
    deleted_backups: tuple[Backup, ...] = ()

    def __post_init__(self) -> None:
        require_text("RestoreEffect", "consequence", self.consequence)
        if len(self.consequence) > MAX_CONSEQUENCE_LENGTH:
            raise OperationTextTooLongError(
                f"RestoreEffect.consequence has {len(self.consequence)} characters; "
                f"the limit is {MAX_CONSEQUENCE_LENGTH}"
            )
