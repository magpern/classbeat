"""Each track's timing class in what the design model sees (ADR 0040 section 3: "the model sees
each track's class"). Input shaping only: the model's draft stays a proposal and
``timing_gate`` enforces the class. Pure: no I/O.

Only a track that is not ``exact`` is marked, with ``"timing": "approximate"`` or
``"unusable"``: in the music summary's tracks (with ``timing_note``, what the marks mean), in a
track's detail and on each interval of the fitted timeline whose track carries one. A map that
marks no track (every map before SD-P6, every mix) gives the summary, the detail and the
timeline back unchanged, so such a class's model input is byte-identical to before.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final

from classbeat.agent import model_input
from classbeat.agent.track_timing_calc import classes

TIMING_NOTE: Final = (
    'A track with "timing": "approximate" plays a recording whose timing is known to about 2 s: '
    "use its BPM, sections and energy, and its drops and breakdowns as about 2 s either way. "
    'A track with "timing": "unusable" has its sections only. On either, give a cue only on an '
    "interval that starts within 2 s of a section start or end (on an approximate track also of "
    "a drop or breakdown), and never a countdown, a beat or bar count or a drop hit there."
)


def annotate_summary(summary: dict[str, Any], m: Mapping[str, Any]) -> dict[str, Any]:
    """The music summary with each non-``exact`` track marked and the note, cut to size again
    by whole tracks as ``model_input`` cuts it."""
    marked = classes(m)
    if not marked:
        return summary
    out = {**summary, "tracks": [dict(t) for t in summary["tracks"]], "timing_note": TIMING_NOTE}
    for track in out["tracks"]:
        if (cls := marked.get(int(track["index"]))) is not None:
            track["timing"] = cls
    total = summary.get("truncated", {}).get("tracks_total", len(summary["tracks"]))
    cut = model_input._truncated(out)  # pyright: ignore[reportPrivateUsage]
    if "truncated" in cut:
        cut["truncated"] = {**cut["truncated"], "tracks_total": total}
    return cut


def annotate_detail(detail: dict[str, Any], m: Mapping[str, Any]) -> dict[str, Any]:
    """A track's detail with its class when it is not ``exact``."""
    cls = classes(m).get(int(detail["index"]))
    return detail if cls is None else {**detail, "timing": cls}


def annotate_timeline(
    timeline: Sequence[dict[str, Any]], m: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """The fitted timeline with each interval on a non-``exact`` track marked."""
    marked = classes(m)
    if not marked:
        return list(timeline)
    return [
        {
            **seg,
            "intervals": [
                i if (cls := marked.get(int(i["track"]))) is None else {**i, "timing": cls}
                for i in seg["intervals"]
            ],
        }
        for seg in timeline
    ]
