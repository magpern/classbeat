"""The energy of a music section as the model sees it (ADR 0010, 2026-11-13 note).

mowl-workouts' Spotify map gives each section an ``energy`` from 0 (the quietest part of its own
track) to 1 (the loudest), ranked by Spotify's loudness within the track; it says nothing across
tracks and holds no audio. This module turns that number into what a view may carry: a plain
float clamped to 0..1 and rounded to 2 decimals, or nothing. A map analysed before section energy,
a one-section track and a flat-sounding track have none, and a Swedebeat mix never does.

Pure: no I/O. ``model_input.track_detail`` shows one number per section; ``songs.analysed_pool``
merges a pool track's sections into at most ``MAX_SECTIONS`` spans (``pool_sections``). The two
guidance lines are the stage prompts' wording of how to use it.
"""

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Final

MAX_SECTIONS: Final = 8  # spans shown per pool track

DESIGN_GUIDANCE: Final = (
    "Where a track's sections show an energy (0 is the quietest part of that track, 1 the "
    "loudest; it compares a track's own sections, never one song with another), put the "
    "warm-up, the recoveries and the cooldown on its quiet sections and the hard work "
    "intervals on its loud ones."
)
SONGS_GUIDANCE: Final = (
    "A pool track's sections are [from_sec, to_sec, energy] spans, energy a rank from 0 (the "
    "quietest part of that track) to 1 (the loudest) within the track only, so every song "
    "reaches both ends and none is louder than another. Whole songs play: compare where each "
    "song's quiet and loud spans fall and how long they last, and prefer songs with long quiet "
    "stretches for the warmup, recovery and cooldown roles and songs with long loud stretches "
    "for the hard work."
)


def energy_of(section: Mapping[str, Any]) -> float | None:
    """The section's energy clamped to 0..1 and rounded, or ``None`` when it has none."""
    value = section.get("energy")
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return round(min(1.0, max(0.0, float(value))), 2)


def pool_sections(
    sections: Sequence[Mapping[str, Any]], start: float, length_sec: int
) -> tuple[tuple[int, int, float], ...]:
    """One track's ``(from_sec, to_sec, energy)`` spans, seconds from the track's start: the
    map's raw sections of that track (those with a valid energy), the shortest repeatedly merged
    into its shorter neighbour, energy averaged by duration, until at most ``MAX_SECTIONS``
    remain. A track with at most that many keeps every section; a long one keeps its contrast. A
    section without a valid energy is skipped, so a neighbour's span covers its time."""
    spans: list[list[float]] = []  # [begin, end, energy x duration, duration]
    for section in sorted(sections, key=lambda s: float(s["start"])):
        energy = energy_of(section)
        begin, end = float(section["start"]), float(section["end"])
        if energy is not None and math.isfinite(begin) and math.isfinite(end) and end > begin:
            spans.append([begin, end, energy * (end - begin), end - begin])
    while len(spans) > MAX_SECTIONS:
        i = min(range(len(spans)), key=lambda k: spans[k][3])
        right = i < len(spans) - 1 and (i == 0 or spans[i + 1][3] < spans[i - 1][3])
        j = i + 1 if right else i - 1
        a, b = sorted((i, j))
        spans[a] = [spans[a][0], spans[b][1], spans[a][2] + spans[b][2], spans[a][3] + spans[b][3]]
        del spans[b]
    out: list[tuple[int, int, float]] = []
    for begin, end, weighted, duration in spans:
        low = min(length_sec, max(0, round(begin - start)))
        high = min(length_sec, max(0, round(end - start)))
        if high > low:
            out.append((low, high, energy_of({"energy": weighted / duration}) or 0.0))
    return tuple(out)


def fit_pool_answer(out: dict[str, Any], limit: int, total: int) -> None:
    """Cut ``get_pool``'s answer in place to ``limit`` bytes: the ``sections`` of the last tracks
    go first (``sections_omitted_from_track`` is the index of the first track that lost them),
    then whole tracks from the end (``truncated``). The only keys this adds are
    ``sections_omitted_from_track`` (an int) and ``truncated`` (an object with ``tracks_shown``
    and ``tracks_total``), pinned by ``tests/test_section_energy.py``."""
    views: list[dict[str, Any]] = out["tracks"]

    def size() -> int:
        return len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode())

    for i in range(len(views) - 1, -1, -1):
        if size() <= limit:
            break
        if views[i].pop("sections", None) is not None:
            out["sections_omitted_from_track"] = i
    while size() > limit and views:
        views.pop()
        out["truncated"] = {"tracks_shown": len(views), "tracks_total": total}
    if out.get("sections_omitted_from_track", 0) >= len(views):
        out.pop("sections_omitted_from_track", None)  # it named a track that was then cut
