"""The explicit report of a Spotify song pick (ADR 0040 section 6, #1340), between the analysis
stage and ``song_list.build``: the provenance record ``explicit`` and the positions that show the
"Explicit" tag. **Report only**: nothing is filtered, ranked, swapped or blocked on a flag, and no
model sees one.

A new song has Spotify's flag from its search hit (``MatchedPool``) and spotdown's own
``content.explicit`` from its analysis record entry; a kept song of a "Change songs" list has its
parent list's evidence for its slot (``spotdown.explicit_model``).

**A flag never fails a pick**: an error of any type in here (swallowed; only its type is logged)
gives no record and no tag, so the list is stored as it was before the flag existed. The stages
before it (match, analysis) never swallow an error: only this report does, by design.
"""

import logging
from collections.abc import Mapping
from typing import Any

from classbeat.agent import ChosenList
from classbeat.songs.pick_match import MatchedPool
from classbeat.songs.seed import Seeded
from classbeat.spotdown import explicit_model as model

logger = logging.getLogger(__name__)


def explicit_report(
    matched: MatchedPool, chosen: ChosenList, analysis: Mapping[str, Any], seeded: Seeded | None
) -> tuple[dict[str, Any], frozenset[int]]:
    """``(record, shown)``: the provenance to add (``{}`` without evidence) and the 0-based
    positions that show the tag. ``({}, empty)`` on an error of any type."""
    try:
        spotify = {p.spotify_id: p.explicit for p in matched.playlist if p.explicit is not None}
        kept = [(k.slot, k.spotify_id) for k in seeded.kept] if seeded is not None else []
        parent = model.evidence_of(seeded.provenance) if seeded is not None else {}
        found = model.evidence_for(
            [(s.spotify_id, s.new) for s in chosen.songs],
            spotify,
            model.analysis_groups(analysis),
            parent,
            kept,
        )
        return model.record(found), frozenset(p for p, ev in found.items() if ev.shown())
    except Exception as exc:
        logger.warning("explicit report failed", extra={"error_type": type(exc).__name__})
        return {}, frozenset()
