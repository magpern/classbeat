"""What a revision records about how it was made, as the class API answers it (ADR 0007 section 12,
ADR 0029 section 4). Split out of ``classes.schemas``: no run ids, no hashes, no lease data."""

from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from classbeat.agent.interpretation_record import InterpretationItem, InterpretationList


class UsageOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


class ProvenanceOut(BaseModel):
    """What a revision records about how it was made, minus the run ids. Unknown keys are
    dropped, never passed through."""

    model_config = ConfigDict(extra="ignore")
    origin: str | None = None
    base_revision_id: UUID | None = None
    request_message_id: UUID | None = None
    provider: str | None = None
    model: str | None = None
    model_answered: str | None = None
    effort: str | None = None
    prompt_version: str | None = None
    classbeat_version: str | None = None
    mowl_version: str | None = None
    map_version: int | None = None
    crossfade_sec: float | None = None
    usage: UsageOut | None = None
    cost_micros: int | None = None
    # How the request was read (ADR 0029 section 4): closed items, the frontend words them by code.
    interpretation: InterpretationList | None = None

    @field_validator("interpretation", mode="before")
    @classmethod
    def _readable_or_absent(cls, value: object) -> object:
        """A stored list that no longer checks is left out; it never fails the revision's read."""
        if not isinstance(value, list):
            return None
        try:
            return [InterpretationItem.model_validate(item) for item in cast(list[Any], value)]
        except ValidationError:
            return None
