"""A studio track's cut candidates as the model sees them (#1328; ADR 0040 section 5). Pure: no
I/O.

Each point is ``{"at_sec", "kind", "use"}`` in the track's own seconds, with ``label`` for a
labelled segment's start (the schema's own words only). ``use`` is ``"trusted"`` for a candidate
that passes the trust gate (``studio.mix_cut_policy.trusted``) and ``"hint"`` for every other one,
marked as a hint as ``spotdown.analysis_hints_policy`` marks them: a guess, never a reason on its
own. Downbeats are left out (the model has the bar grid: ``first_downbeat_sec`` and ``bar_sec``).

- ``edge_points``: the content start and end, silence and fade edges (the music summary), at most
  ``MAX_EDGES``;
- ``all_points``: every kind but downbeats (a track's detail), at most ``MAX_POINTS``, the earliest
  first.
"""

from typing import Any, Final

from classbeat.studio.mix_cut_policy import trusted
from classbeat.studio.mix_facts_model import TrackFacts

MAX_EDGES: Final = 16
MAX_POINTS: Final = 120
EDGE_KINDS: Final = frozenset({
    "content_start", "content_end", "silence_start", "silence_end",
    "fade_in_start", "fade_in_end", "fade_out_start", "fade_out_end",
})  # fmt: skip


def _points(facts: TrackFacts, kinds: frozenset[str] | None, cap: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in facts.candidates:
        if c.kind == "downbeat" or (kinds is not None and c.kind not in kinds):
            continue
        point: dict[str, Any] = {
            "at_sec": round(c.at_s, 2),
            "kind": c.kind,
            "use": "trusted" if trusted(c, facts) else "hint",
        }
        if c.label is not None:
            point["label"] = c.label
        out.append(point)
    return out[:cap]


def edge_points(facts: TrackFacts) -> list[dict[str, Any]]:
    return _points(facts, EDGE_KINDS, MAX_EDGES)


def all_points(facts: TrackFacts) -> list[dict[str, Any]]:
    return _points(facts, None, MAX_POINTS)
