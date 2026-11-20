"""Whether a cue's text gives a beat-, bar- or drop-precise instruction (ADR 0040 section 3: "the
design validator refuses any beat- or bar-precise instruction on a track that is not exact. That
covers cue times, drop hits and countdowns"). Pure: no I/O.

Free text cannot be checked by listing the precise phrasings, so the check is an allowlist for
numbers and a fixed list for the idioms that carry no number:

- ``number``: a number that no section-level use binds (``timing_cue_binding_policy``: a zone,
  gear or level number, minutes, rpm, percent; everything else, seconds and counts included, is
  refused);
- ``countdown``, ``beat_or_bar``, ``drop_hit``: an idiom (``timing_cue_idiom_policy``).

When in doubt a cue is refused: the correction round rewords a refused section-level cue, while
a missed precise cue reaches the instructor.
"""

from typing import Literal

from classbeat.agent.timing_cue_binding_policy import unbound
from classbeat.agent.timing_cue_idiom_policy import IdiomKind, idiom_kind

PreciseKind = IdiomKind | Literal["number"]


def precise_kind(text: str) -> PreciseKind | None:
    """The kind of beat-, bar- or drop-precise instruction ``text`` gives, or ``None``."""
    folded = text.casefold()
    if found := idiom_kind(folded):
        return found
    return "number" if unbound(folded) else None
