"""What a pending chat message becomes (ADR 0038 section 8, #1186). Pure.

A message sent while one of the class's jobs is queued or running waits as ``pending``. When it is
submitted, the **state of the class** decides, never a guess:

| State when it is submitted | The message becomes |
|---|---|
| a song list is waiting for approval | a **change**, which replaces the waiting job (section 6) |
| the current revision is a sketch, no song list waits | a revise of the sketch (section 4) |
| the current revision is a draft (after phase 2, or a soundless class) | an ordinary revise |
| anything else | ``failed`` with ``not_changeable`` |

A job that is still queued or running is not a state of this table: the message stays pending
(``BUSY``) and the page submits it once the job has ended.
"""

from dataclasses import dataclass
from typing import Final, Literal

from classbeat.design.revision_ready_policy import SKETCH

Action = Literal["busy", "change", "sketch_revise", "revise", "not_changeable"]
BUSY: Final = "busy"
NOT_CHANGEABLE: Final = "not_changeable"


@dataclass(frozen=True, slots=True)
class ClassState:
    running: bool  # a job of the class is queued or running
    waiting: bool  # a song list waits for approval
    current_kind: str | None  # the current revision's kind; None without one
    current_origin: str | None  # its ``provenance.origin``


def action(state: ClassState) -> Action:
    """The table's row for ``state``."""
    if state.running:
        return BUSY
    if state.waiting:
        return "change"
    if state.current_kind == "draft":
        return "sketch_revise" if state.current_origin == SKETCH else "revise"
    return NOT_CHANGEABLE
