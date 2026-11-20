"""The inputs of ``timing_class_policy``: what is played, what spotdown analysed, the recording
match, the alignment, a beat-level comparison and the MOWL anchor check. Pure: no I/O."""

from dataclasses import dataclass
from typing import Final, Literal

TimingClass = Literal["exact", "approximate", "unusable"]
PlayedKind = Literal["swedebeat_track", "user_upload", "swedebeat_mix", "spotify"]
AnchorCheck = Literal["agree", "disagree", "inconclusive"]
HELD_KINDS: Final = frozenset({"swedebeat_track", "user_upload", "swedebeat_mix"})


@dataclass(frozen=True, slots=True)
class Played:
    """What the class plays: its kind, its reference (a SwedeBeat reference, the user's file id,
    the mix id, a Spotify id) and the sha256 of the bytes classbeat holds for it (``None`` for
    Spotify: classbeat never has Spotify's audio)."""

    kind: PlayedKind
    ref: str
    sha256: str | None


@dataclass(frozen=True, slots=True)
class Analysed:
    """What spotdown analysed: ``provenance.source.kind`` and ``provenance.audio``, plus the played
    item classbeat uploaded these bytes as (``None`` unless classbeat uploaded them)."""

    source_kind: str
    sha256: str
    fingerprint: str | None
    uploaded_as: tuple[PlayedKind, str] | None


@dataclass(frozen=True, slots=True)
class RecordingMatch:
    """The ``/v2/match`` candidate the source was chosen as, and the decoded duration check."""

    band: str
    confidence: float
    duration_check: str
    decoded_check: str | None = None  # ``provenance.source.decoded_duration_check.result``
    isrc_equal: bool | None = None  # evidence only: never decides a class


@dataclass(frozen=True, slots=True)
class Alignment:
    result: Literal["ok", "failed"]
    residual_s: float | None
    offset_s: float | None


@dataclass(frozen=True, slots=True)
class BeatEquivalence:
    """A beat-grid comparison against a beat-level reference of the played audio."""

    reference: str  # ``beat_grid`` or ``audio_fingerprint``
    median_error_ms: float
    within_window_fraction: float  # of the beats within 50 ms
    whole_track: bool


@dataclass(frozen=True, slots=True)
class TimingInputs:
    played: Played
    analysed: Analysed
    match: RecordingMatch | None = None
    alignment: Alignment | None = None
    equivalence: BeatEquivalence | None = None
    anchors: AnchorCheck | None = None  # classbeat's MOWL section cross-check (``anchor_calc``)
