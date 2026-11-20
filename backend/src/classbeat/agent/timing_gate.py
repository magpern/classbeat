"""The design validator's timing check (ADR 0040 section 3): every fitted draft of a class with
music goes through ``refuse_precise`` (``design.check``, ``running.check_running``, and so the
design, revise, composer and refit paths that call them).

It reads each track's class from the map (``track_timing_calc.classes``), places each cue where
the fitted class starts its interval, and hands the cues on non-``exact`` tracks to
``timing_cue_policy``. A refused cue is ``ToolError('invalid_draft')`` with the policy's
problems, so the model's correction round fixes it and nothing the model states can lift it.
A map with no class on any track (every map before SD-P6, every mix) returns at once: such a
class is checked exactly as before. Pure: no I/O.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from classbeat.agent import track_timing_calc as timing
from classbeat.agent.schemas import RunningDraft, WorkoutDraft
from classbeat.agent.stage import ToolError
from classbeat.agent.timing_cue_policy import CueAt, problems


def cues_on_tracks(
    cues: Sequence[str], fitted: Mapping[str, Any], m: Mapping[str, Any]
) -> list[CueAt]:
    """Each non-empty cue (in interval order, as ``cues``) on each non-``exact`` track playing
    where the fitted class starts its interval."""
    classes = timing.classes(m)
    out: list[CueAt] = []
    t = 0
    intervals = [i for s in fitted["segments"] for i in s["intervals"]]
    for number, (interval, text) in enumerate(zip(intervals, cues, strict=True), start=1):
        if text:
            for track in sorted(timing.playing_at(m, t)):
                cls = classes.get(track)
                if cls is not None:
                    anchors = timing.anchors(m, track, cls)
                    out.append(CueAt(number, float(t), track, cls, anchors, text))
        t += int(interval["sec"])
    return out


def refuse_precise(cues: Sequence[str], fitted: Mapping[str, Any], m: Mapping[str, Any]) -> None:
    """``ToolError('invalid_draft')`` when a cue breaks its track's timing class; else nothing."""
    if not timing.classes(m):
        return
    found = problems(cues_on_tracks(cues, fitted, m))
    if found:
        raise ToolError("invalid_draft", found)


def draft_cues(draft: WorkoutDraft | RunningDraft) -> list[str]:
    """A cycling or running draft's cues, in interval order."""
    return [i.cue for s in draft.segments for i in s.intervals]
