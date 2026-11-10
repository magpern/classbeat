"""The change summary of a revision (ADR 0007 section 3), computed by the backend from the two
stored drafts, never by a model: data, not text (the page words it).

Segments are matched by name and kind in order (the n-th "Climb" with the n-th "Climb"), so an
inserted segment does not turn every later one into a change. Intervals of matched segments are
compared by position and fields (``sec``, ``zone``, ``rpm``, ``standing``, ``game``,
``ftp_test``, and a running class's ``gradient``); the intervals of an added or removed segment
count as added or removed. Duration and TSS before and after come from ``mowl.workout`` on the
fitted drafts. ``text_changed`` says that a name, description or cue differs, so a revision that
only rewords is not shown as no change. The summary is a few hundred bytes
(``class_messages.change`` allows 1024).

``game_changes`` names each game that appeared, went or changed type (ADR 0029 section 3): the
segment (counted from 1, in the new draft; in the old one for a removed segment) and the game
before and after, ``None`` for no game. Games are compared at the same position of a matched
segment; the extra intervals of a longer or shorter segment, and those of an added or removed
segment, count as games added or removed. The page words it ("Segment 3: Power Pull to Overflow").
At most ``MAX_GAME_CHANGES`` are listed, so the summary stays well inside the 1024 bytes.
"""

from collections import Counter
from typing import Any, Final

from mowl import workout

from classbeat.agent.stored_draft import stored_draft

# ``gradient`` is a running interval's only (ADR 0018); a cycling draft never has it.
FIELDS: Final = ("sec", "zone", "rpm", "standing", "game", "ftp_test", "gradient")
MAX_BYTES: Final = 1024
MAX_GAME_CHANGES: Final = 8  # about 60 bytes each


def _spec(content: dict[str, Any], sport: str) -> dict[str, Any]:
    return stored_draft(content, sport).to_spec()


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


def _game_changes(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    removed: list[dict[str, Any]],
    added: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The games that differ, in segment order (see the module docstring)."""
    number = {id(seg): n for n, seg in enumerate(after, 1)}
    number.update({id(seg): n for n, seg in enumerate(before, 1) if id(seg) not in number})
    found: list[tuple[int, str | None, str | None]] = []
    nothing: dict[str, Any] = {"intervals": []}
    for old, new in pairs:
        found += _position_games(number[id(new)], old, new)
    for seg in removed:
        found += _position_games(number[id(seg)], seg, nothing)
    for seg in added:
        found += _position_games(number[id(seg)], nothing, seg)
    found.sort(key=lambda c: c[0])
    return [{"segment": n, "before": was, "after": now} for n, was, now in found[:MAX_GAME_CHANGES]]


def _position_games(
    n: int, old: dict[str, Any], new: dict[str, Any]
) -> list[tuple[int, str | None, str | None]]:
    a, b = old["intervals"], new["intervals"]
    out: list[tuple[int, str | None, str | None]] = []
    for k in range(max(len(a), len(b))):
        was = a[k].get("game") if k < len(a) else None
        now = b[k].get("game") if k < len(b) else None
        if was != now:
            out.append((n, was, now))
    return out


def _texts(content: dict[str, Any]) -> Counter[str]:
    out = [f"n:{content['name']}", f"d:{content.get('description', '')}"]
    for seg in content["segments"]:
        for i in seg["intervals"]:
            out.append(f"c:{seg['name']}:{i.get('cue', '')}")
    return Counter(out)


def summarize(base: dict[str, Any], new: dict[str, Any], sport: str = "cycling") -> dict[str, Any]:
    """The change from ``base`` to ``new`` (both ``class_revisions.content`` of a class of
    ``sport``)."""
    before, after = _spec(base, sport), _spec(new, sport)
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
        "game_changes": _game_changes(before["segments"], after["segments"], pairs, removed, added),
    }
    return out
