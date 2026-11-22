"""The record of a song pick's analysis stage, kept in the ``song_list`` revision's provenance under
``spotdown_analysis`` (ADR 0040 section 6, plan section 4a; SD-P6 stage B, #1303). Pure: no I/O.

Written for every Spotify song list (no fallback to MOWL's sections, #1354). Per song of the
list that was analysed (by its position; a kept song carries its seed list's entry,
``song_carry_calc``):

- ``use``: ``spotdown`` (an ``approximate`` analysis is the song's timing), the only value;
- for an analysis: its id, the analyser (name, version, config hash), the audio identity, the
  source **kind** only, the timing class with its reason and evidence (``timing_class_policy``),
  the fit (``track_fit_policy``) and whether it was accepted with a warning; and, for a song whose
  timing is spotdown's, the document's own ``content.explicit`` when it carries it (``explicit``,
  ``explicit_model.spotdown_group``; report only, #1340).

Per refinement swap: the position, the original as it was chosen (Spotify id, title, artist:
what an undo needs, as ``song_match_record_model``), the fit reasons and the round. The policy
versions of ``provenance_model.POLICY_VERSIONS`` and this stage's.

**Never:** a source reference (video id), a ``cache_scope``, a ``client_ref``, a URL, a job id
or a key. ``timing_marks`` gives what the design map's ``track_timing_calc.stamp`` needs.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from classbeat.spotdown.provenance_model import POLICY_VERSIONS
from classbeat.spotdown.provenance_read_calc import AudioIdentity
from classbeat.spotdown.song_analysis_policy import POLICY_VERSION
from classbeat.spotdown.timing_class_policy import TimingDecision
from classbeat.spotdown.track_fit_policy import Fit

KEY: Final = "spotdown_analysis"
RECORD_VERSION: Final = 2  # 2: every entry is spotdown's timing; no ``sections`` use (#1354)
SOURCE_KIND: Final = "youtube"  # the one source this stage analyses

Use = Literal["spotdown"]  # the one timing source (ADR 0040 amendment, #1354)


@dataclass(frozen=True, slots=True)
class Analysed:
    """A song's analysis, as the record keeps it."""

    analysis_id: str
    analyzer: tuple[str, str, str | None]  # name, version, config hash
    audio: AudioIdentity
    decision: TimingDecision
    explicit: Mapping[str, Any] | None = None  # spotdown's ``content.explicit``, strict


@dataclass(frozen=True, slots=True)
class SongOutcome:
    use: Use
    analysed: Analysed | None = field(default=None, repr=False)
    fit: Fit | None = None
    warning: bool = False  # accepted although it does not fit (after the last round)


@dataclass(frozen=True, slots=True)
class RefineSwap:
    original_id: str = field(repr=False)
    title: str = field(repr=False)
    artist: str = field(repr=False)
    reasons: tuple[str, ...]
    round: int


def _audio(a: AudioIdentity) -> dict[str, Any]:
    return {
        "sha256": a.sha256,
        "fingerprint": a.fingerprint,
        "codec": a.codec,
        "sample_rate_hz": a.sample_rate_hz,
        "channels": a.channels,
        "file_duration_s": a.file_duration_s,
        "content_duration_s": a.content_duration_s,
    }


def _song(position: int, out: SongOutcome) -> dict[str, Any]:
    entry: dict[str, Any] = {"position": position, "use": out.use}
    if (a := out.analysed) is not None:
        name, version, config = a.analyzer
        entry |= {
            "analysis_id": a.analysis_id,
            "analyzer": {"name": name, "version": version, "config_hash": config},
            "audio": _audio(a.audio),
            "source_kind": SOURCE_KIND,
            "timing": {
                "class": a.decision.timing_class,
                "reason": a.decision.reason,
                "evidence": dict(a.decision.evidence),
                "policy_version": a.decision.policy_version,
            },
        }
    if out.use == "spotdown" and out.analysed is not None and out.analysed.explicit is not None:
        entry["explicit"] = dict(out.analysed.explicit)
    if out.fit is not None:
        entry["fit"] = {"fits": out.fit.fits, "reasons": list(out.fit.reasons)}
    if out.warning:
        entry["warning"] = True
    return entry


def _swap(position: int, swap: RefineSwap) -> dict[str, Any]:
    original = {"spotify_id": swap.original_id, "title": swap.title, "artist": swap.artist}
    return {
        "position": position,
        "original": original,
        "reasons": list(swap.reasons),
        "round": swap.round,
    }


def analysis_record(
    outcomes: Mapping[int, SongOutcome], swaps: Mapping[int, RefineSwap], *, rounds: int
) -> dict[str, Any]:
    """``{KEY: record}``; ``outcomes`` and ``swaps`` by position in the list."""
    record = {
        "v": RECORD_VERSION,
        "policy_version": POLICY_VERSION,
        "policy_versions": dict(POLICY_VERSIONS),
        "rounds": rounds,
        "songs": [_song(p, outcomes[p]) for p in sorted(outcomes)],
        "swaps": [_swap(p, swaps[p]) for p in sorted(swaps)],
    }
    return {KEY: record}


def timing_marks(provenance: Mapping[str, Any]) -> list[tuple[int, str, str]]:
    """``(position, class, source kind)`` of every song whose timing is spotdown's, for
    ``track_timing_calc.stamp``; empty for a list without the record. Strict: an entry that is
    not exactly a ``spotdown`` use with an ``approximate`` class is not marked (stage D then fails
    its song ``analysis_failed``: never MOWL's sections, #1386)."""
    record = provenance.get(KEY)
    songs: Sequence[Any] = record.get("songs", ()) if isinstance(record, dict) else ()
    marks: list[tuple[int, str, str]] = []
    for entry in songs:
        if not isinstance(entry, dict) or entry.get("use") != "spotdown":
            continue
        timing, position = entry.get("timing"), entry.get("position")
        cls = timing.get("class") if isinstance(timing, dict) else None
        if cls == "approximate" and isinstance(position, int) and not isinstance(position, bool):
            marks.append((position, "approximate", SOURCE_KIND))
    return marks
