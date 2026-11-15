"""Rules for the format 1.1 beat-counted position pattern (interval.pattern).

Pure, no I/O. Twin of ts/src/pattern.ts. Errors join the semantic rules;
`pattern_without_bpm` is a warning, reported only by `check_warnings`, never by `check_document`.
"""

from __future__ import annotations

from typing import Any

Violation = tuple[str, str]


def check_patterns(doc: dict[str, Any], out: list[Violation]) -> None:
    """Errors: the two lists differ in length, or `position` is not the pattern's first position."""
    for i, iv in enumerate(doc["intervals"]):
        p = iv.get("pattern")
        if p is None:
            continue
        if len(p["positions"]) != len(p["beats"]):
            out.append(("pattern_length_mismatch", f"/intervals/{i}/pattern/beats"))
        if "position" in iv and iv["position"] != p["positions"][0]:
            out.append(("pattern_position_mismatch", f"/intervals/{i}/position"))


def check_warnings(doc: dict[str, Any]) -> list[Violation]:
    """Warnings of a schema-valid document, sorted.

    An interval with a pattern should lie inside songs that all have a `bpm`; a song window it
    overlaps without one, or no song at all, gives `pattern_without_bpm` at the pattern.
    """
    out: list[Violation] = []
    for i, iv in enumerate(doc["intervals"]):
        if "pattern" not in iv:
            continue
        end = iv["start_ms"] + iv["length_ms"]
        songs = [s for s in doc["songs"] if s["start_ms"] < end and s["end_ms"] > iv["start_ms"]]
        if not songs or any("bpm" not in s for s in songs):
            out.append(("pattern_without_bpm", f"/intervals/{i}/pattern"))
    return sorted(out)
