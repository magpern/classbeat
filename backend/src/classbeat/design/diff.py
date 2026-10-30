"""The change summary of a revision (ADR 0007 section 3), computed by the backend from the two
stored drafts, never by a model: data, not text (the page words it).

Segments are matched by name and kind in order (the n-th "Climb" with the n-th "Climb"), so an
inserted segment does not turn every later one into a change. Intervals of matched segments are
compared by position and fields (``sec``, ``zone``, ``rpm``, ``standing``, ``game``,
``ftp_test``); the intervals of an added or removed segment count as added or removed. Duration
and TSS before and after come from ``mowl.workout`` on the fitted drafts. ``text_changed`` says
that a name, description or cue differs, so a revision that only rewords is not shown as no
change. The summary is a few hundred bytes (``class_messages.change`` allows 1024).
"""

from collections import Counter
from typing import Any, Final

from mowl import workout

from classbeat.agent.schemas import WorkoutDraft

FIELDS: Final = ("sec", "zone", "rpm", "standing", "game", "ftp_test")
MAX_BYTES: Final = 1024


def _spec(content: dict[str, Any]) -> dict[str, Any]:
    return WorkoutDraft.model_validate(content).to_spec()


def _matched(
    old: list[dict[str, Any]], new: list[dict[str, Any]]
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(pairs, removed, added): segments paired by (name, kind) and occurrence."""
    pool: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for seg in old:
        pool.setdefault((seg["name"], seg["kind"]), []).append(seg)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    added: list[dict[str, Any]] = []
    for seg in new:
        queue = pool.get((seg["name"], seg["kind"]))
        if queue:
            pairs.append((queue.pop(0), seg))
        else:
            added.append(seg)
    removed = [seg for queue in pool.values() for seg in queue]
    return pairs, removed, added


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return all(a.get(f) == b.get(f) for f in FIELDS)


def _texts(content: dict[str, Any]) -> Counter[str]:
    out = [f"n:{content['name']}", f"d:{content.get('description', '')}"]
    for seg in content["segments"]:
        for i in seg["intervals"]:
            out.append(f"c:{seg['name']}:{i.get('cue', '')}")
    return Counter(out)


def summarize(base: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """The change from ``base`` to ``new`` (both ``class_revisions.content``)."""
    before, after = _spec(base), _spec(new)
    pairs, removed, added = _matched(before["segments"], after["segments"])
    changed = grown = shrunk = 0
    for old_seg, new_seg in pairs:
        old_iv, new_iv = old_seg["intervals"], new_seg["intervals"]
        common = min(len(old_iv), len(new_iv))
        changed += sum(not _same(old_iv[n], new_iv[n]) for n in range(common))
        grown += max(len(new_iv) - common, 0)
        shrunk += max(len(old_iv) - common, 0)
    grown += sum(len(s["intervals"]) for s in added)
    shrunk += sum(len(s["intervals"]) for s in removed)
    out: dict[str, Any] = {
        "segments": {"added": len(added), "removed": len(removed)},
        "intervals": {"added": grown, "removed": shrunk, "changed": changed},
        "duration_sec": {
            "before": workout.total_seconds(before),
            "after": workout.total_seconds(after),
        },
        "tss": {"before": round(workout.tss(before), 1), "after": round(workout.tss(after), 1)},
        "text_changed": _texts(base) != _texts(new),
    }
    return out
