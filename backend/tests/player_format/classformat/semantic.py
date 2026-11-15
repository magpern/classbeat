"""Semantic rules of ADR 0003 section 4 that JSON Schema cannot express.

Pure: takes a document that already passed the schema, returns violations. No I/O.
Each violation is a (code, json_pointer) pair; the TypeScript twin returns the same pairs.
"""

from __future__ import annotations

from typing import Any

from .pattern import check_patterns

Violation = tuple[str, str]

RUNNING_MAX_ZONE = 5


def _check_intervals(doc: dict[str, Any], out: list[Violation]) -> None:
    expected = 0
    for i, iv in enumerate(doc["intervals"]):
        if i == 0 and iv["start_ms"] != 0:
            out.append(("intervals_start_not_zero", "/intervals/0/start_ms"))
        elif i > 0 and iv["start_ms"] != expected:
            out.append(("intervals_gap_or_overlap", f"/intervals/{i}/start_ms"))
        expected = iv["start_ms"] + iv["length_ms"]
    if expected != doc["class"]["length_ms"]:
        out.append(("intervals_end_mismatch", "/intervals"))


def _check_songs(doc: dict[str, Any], out: list[Violation]) -> None:
    songs = doc["songs"]
    for i, s in enumerate(songs):
        base = f"/songs/{i}"
        if i == 0 and s["start_ms"] != 0:
            out.append(("songs_first_start_not_zero", f"{base}/start_ms"))
        if i > 0 and s["start_ms"] != songs[i - 1]["end_ms"]:
            out.append(("songs_not_contiguous", f"{base}/start_ms"))
        if s["end_ms"] <= s["start_ms"]:
            out.append(("song_window_empty", f"{base}/end_ms"))
        elif s["end_ms"] - s["start_ms"] > s["length_ms"]:
            out.append(("song_window_longer_than_song", f"{base}/end_ms"))
        _check_song_points(s, base, out)
    if songs and songs[-1]["end_ms"] != doc["class"]["length_ms"]:
        out.append(("songs_last_end_mismatch", f"/songs/{len(songs) - 1}/end_ms"))


def _check_points(
    points: list[dict[str, Any]], length: int, base: str, kind: str, out: list[Violation]
) -> None:
    previous = -1
    for j, p in enumerate(points):
        at = p["at_ms"]
        if at >= length:
            out.append((f"{kind}_outside_song", f"{base}/{j}/at_ms"))
        if at <= previous:
            out.append((f"{kind}_unordered", f"{base}/{j}/at_ms"))
        previous = at


def _check_song_points(song: dict[str, Any], base: str, out: list[Violation]) -> None:
    if "beat_anchors" in song:
        _check_points(
            song["beat_anchors"]["anchors"],
            song["length_ms"],
            f"{base}/beat_anchors/anchors",
            "anchor",
            out,
        )
    if "moments" in song:
        _check_points(
            song["moments"]["items"], song["length_ms"], f"{base}/moments/items", "moment", out
        )


def _check_segments(doc: dict[str, Any], out: list[Violation]) -> None:
    starts = {iv["start_ms"] for iv in doc["intervals"]}
    ends = {iv["start_ms"] + iv["length_ms"] for iv in doc["intervals"]}
    previous_end = 0
    for i, seg in enumerate(doc.get("segments", [])):
        base = f"/segments/{i}"
        if seg["end_ms"] <= seg["start_ms"]:
            out.append(("segment_empty", f"{base}/end_ms"))
        if seg["start_ms"] < previous_end:
            out.append(("segments_overlap_or_unordered", f"{base}/start_ms"))
        if seg["start_ms"] not in starts:
            out.append(("segment_not_on_interval_boundary", f"{base}/start_ms"))
        if seg["end_ms"] not in ends:
            out.append(("segment_not_on_interval_boundary", f"{base}/end_ms"))
        previous_end = max(previous_end, seg["end_ms"])


def _check_zones(doc: dict[str, Any], out: list[Violation]) -> None:
    running = doc["class"]["sport"] == "running"
    seen: set[int] = set()
    for i, z in enumerate(doc["zone_scale"]):
        if z["zone"] in seen:
            out.append(("zone_duplicate", f"/zone_scale/{i}/zone"))
        seen.add(z["zone"])
        if running and z["zone"] > RUNNING_MAX_ZONE:
            out.append(("zone_out_of_range_for_sport", f"/zone_scale/{i}/zone"))
        for key in ("ftp_pct", "hrmax_pct", "rpe"):
            if key in z and z[key][0] > z[key][1]:
                out.append(("range_inverted", f"/zone_scale/{i}/{key}"))
    for i, iv in enumerate(doc["intervals"]):
        if iv["zone"] not in seen:
            out.append(("zone_not_in_scale", f"/intervals/{i}/zone"))
        cadence = iv.get("cadence")
        if isinstance(cadence, dict) and cadence["from"] > cadence["to"]:
            out.append(("range_inverted", f"/intervals/{i}/cadence"))


def _check_totals(doc: dict[str, Any], out: list[Violation]) -> None:
    totals = doc["totals"]
    if totals["length_ms"] != doc["class"]["length_ms"]:
        out.append(("totals_length_mismatch", "/totals/length_ms"))
    scale = {z["zone"] for z in doc["zone_scale"]}
    keys = {int(k) for k in totals["time_in_zone_ms"]}
    if keys != scale:
        out.append(("totals_zone_keys_mismatch", "/totals/time_in_zone_ms"))
        return
    sums: dict[int, int] = {z: 0 for z in scale}
    for iv in doc["intervals"]:
        if iv["zone"] in sums:
            sums[iv["zone"]] += iv["length_ms"]
    for key, value in totals["time_in_zone_ms"].items():
        if sums[int(key)] != value:
            out.append(("totals_time_mismatch", f"/totals/time_in_zone_ms/{key}"))


def check_semantics(doc: dict[str, Any]) -> list[Violation]:
    """Return every semantic violation of a schema-valid class document, sorted."""
    out: list[Violation] = []
    _check_intervals(doc, out)
    _check_songs(doc, out)
    _check_segments(doc, out)
    _check_zones(doc, out)
    _check_totals(doc, out)
    check_patterns(doc, out)
    return sorted(out)
