"""The strict ``exact`` rule and the timing classes (ADR 0040 section 3, plan section 2;
``classbeat.spotdown.timing_class_policy``), table-driven. A false ``exact`` is a hard gate."""

import math
from dataclasses import replace

import pytest

from classbeat.spotdown import anchor_calc
from classbeat.spotdown.timing_class_policy import (
    EXACT_MEDIAN_MS,
    EXACT_WITHIN_FRACTION,
    POLICY_VERSION,
    RESIDUAL_MAX_S,
    decide,
)
from classbeat.spotdown.timing_inputs_model import (
    Alignment,
    Analysed,
    BeatEquivalence,
    Played,
    RecordingMatch,
    TimingInputs,
)

SHA = "a" * 64
OTHER_SHA = "b" * 64
REF = "sb-track-0001"

STUDIO = Played("swedebeat_track", REF, SHA)
UPLOAD = Played("user_upload", "file-1", SHA)
MIX = Played("swedebeat_mix", "mix-1", SHA)
SPOTIFY = Played("spotify", "4uLU6hMCjMI75M1A2tKUQC", None)

UPLOADED_STUDIO = Analysed("upload", SHA, "fp-1", ("swedebeat_track", REF))
YOUTUBE = Analysed("youtube", SHA, "fp-1", None)
CONFIDENT = RecordingMatch("confident", 0.97, "ok", "ok", isrc_equal=True)
ALIGNED = Alignment("ok", 0.3, -0.4)
EQUIVALENT = BeatEquivalence("beat_grid", 5.0, 1.0, True)

YT = TimingInputs(SPOTIFY, YOUTUBE, CONFIDENT, ALIGNED)


def classify(inputs: TimingInputs) -> tuple[str, str]:
    decision = decide(inputs)
    return decision.timing_class, decision.reason


# --- (a) the played recording ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("played", "uploaded_as"),
    [
        (STUDIO, ("swedebeat_track", REF)),
        (UPLOAD, ("user_upload", "file-1")),
        (MIX, ("swedebeat_mix", "mix-1")),
    ],
)
def test_the_played_recording_is_exact(played: Played, uploaded_as: tuple[str, str]) -> None:
    analysed = Analysed("upload", SHA, None, uploaded_as)  # pyright: ignore[reportArgumentType]
    assert classify(TimingInputs(played, analysed)) == ("exact", "played_recording")


@pytest.mark.parametrize(
    ("played", "analysed"),
    [
        # another sha256: other bytes, even of the same reference
        (STUDIO, replace(UPLOADED_STUDIO, sha256=OTHER_SHA)),
        # the same bytes uploaded as another reference
        (replace(STUDIO, ref="sb-track-0002"), UPLOADED_STUDIO),
        # the same bytes uploaded as another kind of item
        (MIX, Analysed("upload", SHA, None, ("swedebeat_track", "mix-1"))),
        # classbeat holds no bytes of the played item
        (replace(STUDIO, sha256=None), UPLOADED_STUDIO),
        # not classbeat's own upload
        (STUDIO, replace(UPLOADED_STUDIO, source_kind="url")),
        (STUDIO, replace(UPLOADED_STUDIO, source_kind="youtube")),
        (STUDIO, replace(UPLOADED_STUDIO, uploaded_as=None)),
        # Spotify is never played from classbeat's bytes
        (SPOTIFY, Analysed("upload", SHA, None, ("spotify", SPOTIFY.ref))),  # pyright: ignore[reportArgumentType]
    ],
)
def test_anything_short_of_the_played_bytes_is_not_exact(
    played: Played, analysed: Analysed
) -> None:
    assert decide(TimingInputs(played, analysed)).timing_class != "exact"


def test_a_fingerprint_only_match_is_not_the_played_recording() -> None:
    # Re-encoded audio: the decoded fingerprint is equal, the bytes are not.
    analysed = Analysed("upload", OTHER_SHA, "fp-same", ("swedebeat_track", REF))
    inputs = TimingInputs(STUDIO, analysed, CONFIDENT, ALIGNED, anchors="agree")
    decision = decide(inputs)
    assert decision.timing_class != "exact"
    assert decision.evidence["sha256_equal"] is False


# --- YouTube is never exact ------------------------------------------------------------------


@pytest.mark.parametrize(
    "inputs",
    [
        YT,
        replace(YT, anchors="agree"),
        replace(YT, match=replace(CONFIDENT, confidence=1.0, isrc_equal=True), anchors="agree"),
        replace(YT, alignment=Alignment("ok", 0.0, 0.0), anchors="agree"),
        # a beat-level comparison is not accepted for a YouTube analysis either
        replace(YT, equivalence=EQUIVALENT, anchors="agree"),
        # even when it claims to be the played studio track with its sha256
        TimingInputs(STUDIO, YOUTUBE, CONFIDENT, ALIGNED, EQUIVALENT, "agree"),
        TimingInputs(STUDIO, replace(YOUTUBE, uploaded_as=("swedebeat_track", REF))),
    ],
)
def test_a_youtube_track_is_never_exact(inputs: TimingInputs) -> None:
    assert decide(inputs).timing_class != "exact"


def test_a_confident_youtube_match_with_an_equal_isrc_and_agreeing_anchors_is_approximate() -> None:
    decision = decide(replace(YT, anchors="agree"))
    assert (decision.timing_class, decision.reason) == ("approximate", "matched_recording")
    assert decision.evidence["isrc_equal"] is True
    assert decision.evidence["anchors"] == "agree"


def test_the_isrc_decides_nothing() -> None:
    for isrc in (True, False, None):
        changed = replace(YT, match=replace(CONFIDENT, isrc_equal=isrc))
        assert decide(changed).timing_class == "approximate"


# --- (b) verified beat-level equivalence --------------------------------------------------


def _equivalent(**changes: object) -> TimingInputs:
    analysed = replace(UPLOADED_STUDIO, sha256=OTHER_SHA)  # not the same bytes: path (b) only
    return TimingInputs(STUDIO, analysed, equivalence=replace(EQUIVALENT, **changes))  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, "exact"),
        ({"median_error_ms": EXACT_MEDIAN_MS, "within_window_fraction": 0.95}, "exact"),
        ({"reference": "audio_fingerprint"}, "exact"),
        ({"median_error_ms": EXACT_MEDIAN_MS + 0.1}, "unusable"),
        ({"within_window_fraction": EXACT_WITHIN_FRACTION - 0.001}, "unusable"),
        ({"whole_track": False}, "unusable"),
        ({"reference": "structure"}, "unusable"),
        ({"reference": "metadata"}, "unusable"),
        ({"median_error_ms": math.nan}, "unusable"),
        ({"within_window_fraction": math.nan}, "unusable"),
    ],
)
def test_beat_equivalence_needs_both_numbers_over_the_whole_track(
    changes: dict[str, object], expected: str
) -> None:
    assert decide(_equivalent(**changes)).timing_class == expected


def test_beat_equivalence_against_a_spotify_track_is_not_exact_yet() -> None:
    analysed = Analysed("url", OTHER_SHA, None, None)
    inputs = TimingInputs(SPOTIFY, analysed, CONFIDENT, ALIGNED, EQUIVALENT)
    assert decide(inputs).timing_class == "approximate"


# --- approximate and unusable --------------------------------------------------------------


APPROX = ("approximate", "matched_recording")


def matched(**changes: object) -> TimingInputs:
    return replace(YT, match=replace(CONFIDENT, **changes))  # pyright: ignore[reportArgumentType]


def aligned(residual: float | None, result: str = "ok") -> TimingInputs:
    return replace(YT, alignment=Alignment(result, residual, 0.0))  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        (YT, APPROX),
        (matched(band="uncertain", confidence=0.7), APPROX),
        (matched(duration_check="tolerable"), APPROX),
        (matched(decoded_check=None), APPROX),
        (aligned(RESIDUAL_MAX_S), APPROX),
        (replace(YT, anchors="inconclusive"), APPROX),
        (matched(band="not_same", confidence=0.4), ("unusable", "not_same_recording")),
        (matched(confidence=1.5), ("unusable", "not_same_recording")),
        (replace(YT, match=None), ("unusable", "no_recording_match")),
        (matched(duration_check="rejected"), ("unusable", "duration_rejected")),
        (matched(decoded_check="rejected"), ("unusable", "duration_rejected")),
        (aligned(RESIDUAL_MAX_S + 0.01), ("unusable", "residual_too_large")),
        (aligned(math.nan), ("unusable", "residual_too_large")),
        (aligned(math.inf), ("unusable", "residual_too_large")),
        (aligned(-0.1), ("unusable", "residual_too_large")),
        (aligned(None), ("unusable", "no_alignment")),
        (replace(YT, alignment=None), ("unusable", "no_alignment")),
        (aligned(None, "failed"), ("unusable", "alignment_failed")),
        (replace(YT, anchors="disagree"), ("unusable", "anchors_disagree")),
        # a held item analysed from other bytes, with no match and no equivalence: unproven
        (
            TimingInputs(STUDIO, replace(UPLOADED_STUDIO, sha256=OTHER_SHA)),
            ("unusable", "no_recording_match"),
        ),
    ],
)
def test_the_approximate_and_unusable_table(
    inputs: TimingInputs, expected: tuple[str, str]
) -> None:
    assert classify(inputs) == expected


def test_a_failed_alignment_wins_over_a_confident_match_and_agreeing_anchors() -> None:
    inputs = replace(YT, alignment=Alignment("failed", None, None), anchors="agree")
    assert classify(inputs) == ("unusable", "alignment_failed")


# --- the evidence is kept with the class ---------------------------------------------------


def test_the_decision_carries_its_evidence_and_version() -> None:
    decision = decide(replace(YT, anchors="agree", equivalence=EQUIVALENT))
    assert decision.policy_version == POLICY_VERSION
    assert decision.evidence == {
        "played_kind": "spotify",
        "source_kind": "youtube",
        "sha256_equal": False,
        "uploaded_as_played": False,
        "anchors": "agree",
        "match_band": "confident",
        "recording_match_confidence": 0.97,
        "duration_check": "ok",
        "decoded_duration_check": "ok",
        "isrc_equal": True,
        "alignment": "ok",
        "residual_s": 0.3,
        "offset_s": -0.4,
        "beat_reference": "beat_grid",
        "beat_median_error_ms": 5.0,
        "beats_within_window": 1.0,
        "beat_whole_track": True,
    }


def test_the_evidence_holds_no_reference_or_identity() -> None:
    decision = decide(TimingInputs(STUDIO, UPLOADED_STUDIO))
    flat = repr(decision.evidence)
    for planted in (REF, SHA, "fp-1"):
        assert planted not in flat


# --- the MOWL anchor check ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("anchors", "boundaries", "offset", "expected"),
    [
        ([20.0, 60.0, 100.0], [20.5, 61.0, 99.0], 0.0, "agree"),
        ([20.0, 60.0, 100.0], [21.0, 61.0, 101.0], -1.0, "agree"),  # moved to the reference clock
        ([20.0, 60.0, 100.0], [30.0, 80.0, 130.0], 0.0, "disagree"),
        ([20.0, 60.0, 100.0, 140.0], [20.0, 60.0, 120.0, 160.0], 0.0, "inconclusive"),  # 0.5
        ([20.0, 60.0], [20.0, 60.0], 0.0, "inconclusive"),  # too few anchors
        ([0.0, 0.5, 179.5], [0.0, 0.5, 179.5], 0.0, "inconclusive"),  # only edges
        ([20.0, 60.0, 100.0], [math.nan], 0.0, "disagree"),
    ],
)
def test_the_anchor_check(
    anchors: list[float], boundaries: list[float], offset: float, expected: str
) -> None:
    share = anchor_calc.agreement(anchors, boundaries, offset, 180.0)
    assert anchor_calc.check(share) == expected


def test_agreeing_anchors_never_lift_an_unusable_track() -> None:
    share = anchor_calc.agreement([20.0, 60.0, 100.0], [20.0, 60.0, 100.0], 0.0, 180.0)
    agree = anchor_calc.check(share)
    assert agree == "agree"
    inputs = replace(YT, alignment=Alignment("ok", 3.0, 0.0), anchors=agree)
    assert classify(inputs) == ("unusable", "residual_too_large")
