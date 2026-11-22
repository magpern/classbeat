"""A Spotify song pick's music data with spotdown's sections (ADR 0040 sections 2, 3 and 8, its
SD-P6 stage D amendment; plan section 2; #1303). Pure: no I/O.

Phase 2 (``classbeat.songs.phase2_spotdown``) holds MOWL's analysis of the final playlist, the map
of ``mowl.spotify.build_map``. For each song the list's stage-B record marks
(``song_analysis_record_model.timing_marks``) it reads that analysis again by its id:

- ``recorded``: what the record says of each marked song (the analysis id, the audio sha256, the
  timing decision and the source kind); an entry that lacks any of them, or a position recorded
  twice, is left out, so stage D fails its song ``analysis_failed`` (never MOWL's sections, #1386);
- ``verified``: the document read is that analysis (the same id and audio sha256, the same source
  kind, which must be ``youtube``: an allowlist) **aligned to this track** (a ``spotify``
  reference with the track's opaque id, ``reference_id``, and its length within
  ``MAX_LENGTH_DIFF_S``);
- ``apply_track``: the track's MOWL sections replaced by spotdown's
  (``spotdown_map_policy.reference_sections``, on the Spotify clock), rebuilt by the library's own
  ``build_map`` with the track's MOWL length and BPM (Spotify's own, of the recording that is
  played: spotdown adds section-level energy, not the tempo), so its drops and breakdowns are the
  loudness steps between those sections as for MOWL's; then the track is stamped
  (``track_timing_calc.stamp``). Only a class that ``track_timing_calc.capped`` reads as
  ``approximate`` is applied: ``unusable`` or an unknown class gives ``None`` (the caller fails
  the song; no stamp), and a ``youtube`` source is never ``exact``.

Every other track, section and event of the map is kept as it was, in order; only the section
numbers (``song``) after the track move. A map whose order is not the library's raises
``ValueError``.
"""

import copy
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from mowl import spotify

from classbeat.agent import track_timing_calc
from classbeat.spotdown import feature_trust_policy as ft
from classbeat.spotdown.provenance_read_calc import audio_identity, sha256_of, source_kind
from classbeat.spotdown.song_analysis_record_model import KEY, timing_marks
from classbeat.spotdown.timing_class_policy import TimingDecision
from classbeat.spotdown.timing_inputs_model import TimingClass

REFERENCE_KIND: Final = "spotify"
# The source kinds whose analysis may replace MOWL's sections of a Spotify track: an allowlist.
SECTION_SOURCES: Final = frozenset({"youtube"})
MAX_LENGTH_DIFF_S: Final = 1.0  # the reference's length against the track's on Spotify
_MOVED: Final = ("start", "end", "first_downbeat", "first_phrase")


def reference_id(spotify_id: str) -> str:
    """The opaque id of a Spotify track as spotdown's alignment reference (no Spotify id leaves)."""
    return hashlib.sha256(f"spotify:{spotify_id}".encode()).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class Recorded:
    position: int
    analysis_id: str
    sha256: str
    decision: TimingDecision
    source_kind: str


def entries_by_position(provenance: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    """The record's entries by position; a position recorded twice is left out (strict)."""
    record = provenance.get(KEY)
    songs = record.get("songs") if isinstance(record, dict) else None
    found: dict[int, list[Mapping[str, Any]]] = {}
    for entry in songs if isinstance(songs, list) else ():
        position = entry.get("position") if isinstance(entry, dict) else None
        if isinstance(position, int) and not isinstance(position, bool):
            found.setdefault(position, []).append(entry)  # pyright: ignore[reportArgumentType]
    return {p: e[0] for p, e in found.items() if len(e) == 1}


def recorded(provenance: Mapping[str, Any]) -> list[Recorded]:
    """Each marked song with its record entry; strict (module docstring)."""
    entries = entries_by_position(provenance)
    out: list[Recorded] = []
    for position, cls, kind in timing_marks(provenance):
        entry = entries.get(position)
        if entry is None:
            continue
        analysis_id, audio, timing = (entry.get(k) for k in ("analysis_id", "audio", "timing"))
        sha = sha256_of(audio.get("sha256")) if isinstance(audio, dict) else None
        if not isinstance(analysis_id, str) or not analysis_id or sha is None:
            continue
        if not isinstance(timing, dict):
            continue
        reason, evidence, version = (
            timing.get(k) for k in ("reason", "evidence", "policy_version")
        )
        if not isinstance(reason, str) or not isinstance(version, str):
            continue
        if not isinstance(evidence, dict):
            continue
        decision = TimingDecision(cast(TimingClass, cls), reason, dict(evidence), version)
        out.append(Recorded(position, analysis_id, sha, decision, kind))
    return out


def verified(doc: Mapping[str, object], rec: Recorded, spotify_id: str, length_s: float) -> bool:
    """Whether ``doc`` is the recorded analysis, aligned to this track (module docstring)."""
    identity = audio_identity(doc)
    if doc.get("analysis_id") != rec.analysis_id or identity is None:
        return False
    if identity.sha256 != rec.sha256:
        return False
    kind = source_kind(doc)
    if kind is None or kind != rec.source_kind or kind not in SECTION_SOURCES:
        return False
    reference = ft.obj(doc, "alignment", "reference")
    if reference is None or reference.get("kind") != REFERENCE_KIND:
        return False
    if reference.get("id") != reference_id(spotify_id):
        return False
    length = ft.num(reference.get("duration_s"), 0.0)
    return length is not None and abs(length - length_s) <= MAX_LENGTH_DIFF_S


def _one_track(
    track: Mapping[str, Any], sections: Sequence[Mapping[str, object]]
) -> dict[str, Any]:
    spotify_id = str(track["spotify_id"])
    row = {
        "Title": track["title"],
        "Artist": track["artist"],
        "BPM": track["bpm"],
        "SpotifyTrackID": spotify_id,
        "Duration": float(track["sec"]) * 1000.0,
    }
    return spotify.build_map([row], {spotify_id: [dict(s) for s in sections]}, 0.0)


def with_sections(
    m: Mapping[str, Any], index: int, sections: Sequence[Mapping[str, object]]
) -> dict[str, Any]:
    """A copy of ``m`` whose track ``index`` (0-based) has ``sections`` (``{start, duration,
    loudness}`` on its own clock) in place of its own; everything else kept."""
    out = copy.deepcopy(dict(m))
    number, track = index + 1, out["tracks"][index]
    if int(track["track"]) != number:
        raise ValueError("the map's tracks are not in play order")
    songs: list[dict[str, Any]] = out["songs"]
    track_of = {s["song"]: s["track"] for s in songs}
    before = [s for s in songs if s["track"] < number]
    old = [s for s in songs if s["track"] == number]
    after = [s for s in songs if s["track"] > number]
    if not old or before + old + after != songs:
        raise ValueError("the map's sections are not in play order")
    events: list[dict[str, Any]] = out["events"]
    ev_before = [e for e in events if track_of[e["song"]] < number]
    ev_old = [e for e in events if track_of[e["song"]] == number]
    ev_after = [e for e in events if track_of[e["song"]] > number]
    if ev_before + ev_old + ev_after != events:
        raise ValueError("the map's events are not in play order")
    t0 = float(old[0]["start"])
    one = _one_track(track, sections)
    first = len(before) + 1
    new = [
        {**s, "song": first + k, "track": number} | {key: round(s[key] + t0, 3) for key in _MOVED}
        for k, s in enumerate(one["songs"])
    ]
    renumbered = {s["song"]: n for n, s in enumerate(before, 1)}
    renumbered |= {s["song"]: first + len(new) + k for k, s in enumerate(after)}
    out["songs"] = [
        *({**s, "song": renumbered[s["song"]]} for s in before),
        *new,
        *({**s, "song": renumbered[s["song"]]} for s in after),
    ]
    starts = [{**e, "song": first} for e in ev_old if e["kind"] == "track"]  # the track change
    moved = [
        {**e, "t": round(float(e["t"]) + t0, 3), "song": first + int(e["song"]) - 1}
        for e in one["events"]
    ]
    out["events"] = [
        *({**e, "song": renumbered[e["song"]]} for e in ev_before),
        *starts,
        *moved,
        *({**e, "song": renumbered[e["song"]]} for e in ev_after),
    ]
    return out


def apply_track(
    m: Mapping[str, Any],
    index: int,
    decision: TimingDecision,
    kind: str,
    sections: Sequence[Mapping[str, object]],
) -> dict[str, Any] | None:
    """``m`` with track ``index`` on spotdown's sections and stamped, or ``None`` when its class
    is not ``approximate`` as ``track_timing_calc`` reads it (MOWL's sections stay)."""
    if track_timing_calc.capped(decision.timing_class, kind) != "approximate":
        return None
    return track_timing_calc.stamp(
        with_sections(m, index, sections), index, decision.timing_class, kind
    )
