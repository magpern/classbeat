"""The input of ``produce_outputs`` (ADR 0036 section 6, SR C6a): the user's approval of one studio
draft for the outputs they chose. It is the approval record; the worker reads it after the fence.

``{class_id, revision_id, content_hash, outputs, swedebeat_connection_id, spotify_connection_id}``:

- ``revision_id`` and ``content_hash`` name the draft (its hash covers the mix spec, section 1);
- ``outputs`` are stored in ``output_choice_policy.OUTPUTS`` order, each once (a repeat or an
  unknown output is a validation error), so the same choice in another order is the same request
  for the ``Idempotency-Key``;
- ``swedebeat_connection_id`` is the SwedeBeat connection the consent was given for
  (``CONNECTIONS``: the jobs API refuses another than ``ctx.connection("swedebeat")``);
  ``spotify_connection_id`` is set exactly when ``spotify_playlist`` is chosen (``admit``);
- ``swedebeat_generation`` is a **server field**: the generation of that connection at the
  approval, which the route's ``GET /auth/session`` checked (``outputs_session``) and which every
  later step compares again. A browser sending it is refused (422).

Nothing here is a name, a body or an id of anything outside: every written value comes from the
stored draft and its approved list.
"""

from typing import ClassVar, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from classbeat.design.revise import Sha256Hex
from classbeat.jobs.inputs import JobInput
from classbeat.studio.output_choice_policy import OUTPUTS, Output, canonical


class ProduceOutputsInput(JobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("swedebeat",)

    revision_id: UUID
    content_hash: Sha256Hex
    outputs: list[Output] = Field(min_length=1, max_length=len(OUTPUTS))
    swedebeat_connection_id: UUID
    spotify_connection_id: UUID | None = None
    swedebeat_generation: int | None = Field(default=None, ge=1)  # server field

    @field_validator("outputs")
    @classmethod
    def _in_order_once(cls, value: list[Output]) -> list[Output]:
        return [o for o in OUTPUTS if o in canonical(value)]

    @model_validator(mode="after")
    def _a_class_and_its_spotify(self) -> Self:
        if self.class_id is None:
            raise ValueError("an output choice names its class")
        if (self.spotify_connection_id is not None) != ("spotify_playlist" in self.outputs):
            raise ValueError("the Spotify connection goes with the Spotify playlist only")
        return self

    @property
    def the_class(self) -> UUID:
        assert self.class_id is not None  # noqa: S101 - the validator above
        return self.class_id
