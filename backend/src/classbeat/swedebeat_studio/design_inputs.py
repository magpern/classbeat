"""The inputs of ``studio_design`` and ``studio_revise`` (ADR 0036 sections 4.4 and 4.5, SR C5).

- ``StudioDesignInput``: ``{class_id, song_list_revision_id, swedebeat_connection_id}``; the
  ``brief`` is a server field, set by ``admit`` from the approving ``studio_songs`` job (the class
  has no brief of its own before its first design). A browser sending it is refused.
- ``StudioReviseInput``: ``{class_id, base_revision_id, request, swedebeat_connection_id}`` and,
  for a **re-plan** (ADR 0036 amendment B6, #1372), the user's ``choices`` (``MixChoices`` as the
  API reads them, the field ``apply_mix_choices`` takes; read and checked against the list at
  admit, ``invalid_input`` otherwise); the ``base_revision_hash`` and ``request_message_id`` are
  server fields, as ``revise_draft``'s.

``CONNECTIONS = ("swedebeat",)``: no MOWL and no Spotify connection. The SwedeBeat one gives the
analyses' cache (held, ADR 0002) and, for a design, the downloads of the cache misses.
"""

from typing import Any, ClassVar, Self
from uuid import UUID

from pydantic import model_validator

from classbeat.agent.brief import Brief
from classbeat.design.revise import Request, Sha256Hex
from classbeat.jobs.inputs import ModelJobInput


class StudioClassInput(ModelJobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("swedebeat",)

    swedebeat_connection_id: UUID

    @model_validator(mode="after")
    def _a_class_of_its_own(self) -> Self:
        if self.class_id is None:
            raise ValueError("a studio design names its class")
        return self

    @property
    def the_class(self) -> UUID:
        assert self.class_id is not None  # noqa: S101 - the validator above
        return self.class_id


class StudioDesignInput(StudioClassInput):
    song_list_revision_id: UUID
    brief: Brief | None = None  # server field: the approving job's


class StudioReviseInput(StudioClassInput):
    base_revision_id: UUID
    request: Request
    choices: dict[str, Any] | None = None  # a re-plan's ``MixChoices``, checked at admit
    base_revision_hash: Sha256Hex | None = None  # server field
    request_message_id: UUID | None = None  # server field
