"""The alignment facts of a Spotify draft's songs and the instructor's corrections against them
(ADR 0042 sections 2 and 7.4; CP-S3, #1398). Pure: no I/O.

**The two are never merged.** spotdown's alignment (``Fact``: the analysed file's offset to the
Spotify recording, read from the song list's ``spotdown_analysis`` record) is a fact of the
analysis. The instructor's correction (``spotify_offset_corrections``) is a separate number set by
ear, stored with the analysis it was set against (``SetAgainst``). A correction never writes an
analysis, and the audition cursor subtracts the two separately.

**Listen again.** A correction whose ``SetAgainst`` is not the analysis now (another ``map_version``
or another analysed file, ``sha256``) is ``listen_again``: shown, not applied until the instructor
confirms it again, which stores it against the analysis now. A correction with no ``Fact`` to
compare with is ``listen_again`` too (the analysis is gone).

A song with no usable alignment (no record entry, a failed alignment, a non-finite or absurd
offset) has ``offset_ms`` ``None``: the transport shows it as ``unaligned``.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from classbeat.spotdown.provenance_read_calc import sha256_of
from classbeat.spotdown.song_sections_calc import entries_by_position

MAX_OFFSET_MS: Final = 60_000  # a correction, as the column's check
MAX_ALIGNMENT_MS: Final = 3_600_000  # spotdown's own bound on ``offset_s`` (an hour)
TIMING_CLASSES: Final = frozenset({"exact", "approximate", "unusable"})

CorrectionState = Literal["current", "listen_again"]


@dataclass(frozen=True, slots=True)
class Fact:
    """What the analysis record says of one song (by its 0-based position in the list)."""

    position: int
    analysis_id: str | None
    sha256: str | None
    timing_class: str | None
    offset_ms: int | None


@dataclass(frozen=True, slots=True)
class SetAgainst:
    """The analysis a correction was set against."""

    map_version: int | None
    sha256: str

    def stored(self) -> dict[str, Any]:
        return {"map_version": self.map_version, "sha256": self.sha256}


def _dict(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownVariableType]


def _offset_ms(evidence: Mapping[str, Any]) -> int | None:
    """spotdown's ``offset_s`` as whole ms; None unless its alignment is ``ok`` and sane."""
    if evidence.get("alignment") != "ok":
        return None
    seconds = evidence.get("offset_s")
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        return None
    if not math.isfinite(seconds) or abs(seconds * 1000) > MAX_ALIGNMENT_MS:
        return None
    return round(seconds * 1000)


def _fact(position: int, entry: Mapping[str, Any]) -> Fact:
    analysis_id = entry.get("analysis_id")
    timing = _dict(entry.get("timing"))
    cls = timing.get("class")
    return Fact(
        position,
        analysis_id if isinstance(analysis_id, str) and 0 < len(analysis_id) <= 100 else None,
        sha256_of(_dict(entry.get("audio")).get("sha256")),
        cls if isinstance(cls, str) and cls in TIMING_CLASSES else None,
        _offset_ms(_dict(timing.get("evidence"))),
    )


def facts_of(provenance: Mapping[str, Any]) -> dict[int, Fact]:
    """The facts by 0-based position; a song the record does not hold once is absent."""
    return {p: _fact(p, e) for p, e in entries_by_position(provenance).items()}


def map_version_of(provenance: Mapping[str, Any]) -> int | None:
    """The draft's ``map_version`` (a revision's provenance); None when it has none."""
    value = provenance.get("map_version")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def crossfade_ms_of(provenance: Mapping[str, Any], list_crossfade_sec: float) -> int:
    """The class's crossfade constant ``C``: the draft's own map's, else the song list's."""
    seconds = provenance.get("crossfade_sec")
    if isinstance(seconds, bool) or not isinstance(seconds, int | float) or seconds < 0:
        seconds = list_crossfade_sec
    return round(float(seconds) * 1000)


def set_against(fact: Fact | None, map_version: int | None) -> SetAgainst | None:
    """What a correction made now is set against; None without an analysed file to name."""
    return None if fact is None or fact.sha256 is None else SetAgainst(map_version, fact.sha256)


def stored_set_against(raw: object) -> SetAgainst | None:
    """A stored ``set_against_analysis``; None when it is not the shape the table's check keeps."""
    value = _dict(raw)
    sha, version = sha256_of(value.get("sha256")), value.get("map_version")
    if sha is None or (
        version is not None and (isinstance(version, bool) or not isinstance(version, int))
    ):
        return None
    return SetAgainst(version, sha)


def state_of(stored: SetAgainst | None, now: SetAgainst | None) -> CorrectionState:
    """``current`` only when the correction was set against exactly the analysis now."""
    return "current" if stored is not None and stored == now else "listen_again"


def valid_offset(offset_ms: int) -> bool:
    """A correction is a non-zero whole number of ms within a minute either way."""
    return (
        isinstance(offset_ms, int)  # pyright: ignore[reportUnnecessaryIsInstance]
        and not isinstance(offset_ms, bool)
        and offset_ms != 0
        and abs(offset_ms) <= MAX_OFFSET_MS
    )
