"""The mix-choice fields of a studio draft's timeline (ADR 0036 amendment B, B3, B4 and B10, #1369),
what the mix screen reads to show each song and to pre-fill and gate the choices:

- per track (``TrackMix``): its **analysed** length (the stored analysis's, not the list's
  SwedeBeat ``duration_sec``) and its stored timing class (``None``: none stored);
- per draft (``DraftMix``): the stored spec's ``spec_digest`` (``mix_apply_calc.spec_digest``, the
  one Approve compares with the preview's), the ``proposal_revision_id`` the choices apply to
  (``None``: ``no_proposal``), whether the spec ``is_plain`` (whole songs,
  ``mix_choice_policy.is_plain``) and, for an applied draft, its recorded ``choices`` (to pre-fill
  the screen as toggles, never applied by themselves).

Only ``GET /timeline`` answers them, and it answers only for a studio draft.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from classbeat.spotdown.timing_inputs_model import TimingClass


class TrackMix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    length_sec: float = Field(gt=0)
    timing_class: TimingClass | None


class DraftMix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_revision_id: str | None
    is_plain: bool
    choices: dict[str, Any] | None
