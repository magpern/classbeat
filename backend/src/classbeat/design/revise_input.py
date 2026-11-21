"""The input of the ``revise_draft`` job kind (ADR 0007 section 3) and the request types other
kinds share. Moved out of ``classbeat.design.revise`` unchanged (#1303, the code-structure rule
"split on touch"); ``revise`` re-exports them.
"""

from typing import Annotated, ClassVar, Final, Self
from uuid import UUID

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from classbeat.agent.schemas import clean_text
from classbeat.design.revise_sketch import SketchBaseField
from classbeat.jobs.inputs import ModelJobInput

MAX_REQUEST: Final = 2000


def _visible(value: str) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        raise ValueError("must have visible text")
    return cleaned


Request = Annotated[str, Field(min_length=1, max_length=MAX_REQUEST), AfterValidator(_visible)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ReviseDraftInput(SketchBaseField, ModelJobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl",)

    base_revision_id: UUID
    request: Request
    mowl_connection_id: UUID
    # Server fields: only ``admit`` sets them (a browser sending one is refused).
    base_revision_hash: Sha256Hex | None = None
    request_message_id: UUID | None = None

    @model_validator(mode="after")
    def _a_class_of_its_own(self) -> Self:
        if self.class_id is None:
            raise ValueError("a revision names its class")
        return self
