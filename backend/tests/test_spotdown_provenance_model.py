"""The provenance kept per track (ADR 0040 section 6, plan section 4a) and the reuse rule
(section 7: never on the fingerprint alone); ``classbeat.spotdown.provenance_model``. It also runs
the vendored examples through the timing policy end to end."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from classbeat.spotdown import spotdown_map_policy, timing_class_policy, track_fit_policy
from classbeat.spotdown.provenance_model import POLICY_VERSIONS, may_reuse, provenance_of
from classbeat.spotdown.provenance_read_calc import (
    MALFORMED,
    AudioIdentity,
    alignment,
    analysed,
    audio_identity,
    decoded_duration_check,
)
from classbeat.spotdown.timing_class_policy import decide
from classbeat.spotdown.timing_inputs_model import (
    Alignment,
    Played,
    RecordingMatch,
    TimingInputs,
)

EXAMPLES = Path(__file__).resolve().parent / "contracts" / "spotdown" / "examples"
SHA = "a" * 64
FULL: dict[str, Any] = json.loads((EXAMPLES / "analysis-v1.full.json").read_text("utf-8"))
MINIMAL: dict[str, Any] = json.loads((EXAMPLES / "analysis-v1.minimal.json").read_text("utf-8"))
SPOTIFY = Played("spotify", "4uLU6hMCjMI75M1A2tKUQC", None)
STUDIO = Played("swedebeat_track", "sb-track-0001", SHA)
CONFIDENT = RecordingMatch("confident", 0.91, "ok", None, isrc_equal=True)


def youtube_decision(doc: dict[str, Any]) -> timing_class_policy.TimingDecision:
    seen = analysed(doc, None)
    assert seen is not None
    return decide(TimingInputs(SPOTIFY, seen, CONFIDENT, alignment(doc), anchors="agree"))


def test_the_full_youtube_example_is_approximate_end_to_end() -> None:
    decision = youtube_decision(FULL)
    assert (decision.timing_class, decision.reason) == ("approximate", "matched_recording")
    assert decision.evidence["residual_s"] == 0.3


def test_the_minimal_upload_example_is_exact_only_as_the_played_bytes() -> None:
    seen = analysed(MINIMAL, ("swedebeat_track", STUDIO.ref))
    assert seen is not None
    assert decide(TimingInputs(STUDIO, seen)).timing_class == "exact"
    other = analysed(MINIMAL, ("swedebeat_track", "sb-track-0002"))
    assert other is not None
    assert decide(TimingInputs(STUDIO, other)).timing_class == "unusable"


def test_a_failed_alignment_is_read_from_the_provenance() -> None:
    doc = copy.deepcopy(FULL)
    del doc["alignment"]
    doc["provenance"]["alignment"] = {
        "alignment_version": "1",
        "reference": {"kind": "spotify", "id": SPOTIFY.ref, "duration_s": 30.0},
        "result": "failed",
        "hypotheses": [],
    }
    assert alignment(doc) == Alignment("failed", None, None)
    assert youtube_decision(doc).timing_class == "unusable"
    assert alignment(MINIMAL) is None
    assert alignment(FULL) == Alignment("ok", 0.3, -0.4)


def test_the_decoded_duration_check_is_read() -> None:
    doc = copy.deepcopy(FULL)
    assert decoded_duration_check(doc) is None
    doc["provenance"]["source"]["decoded_duration_check"] = {
        "expected_s": 12.0,
        "expected_from": "match",
        "file_delta_s": 0.1,
        "content_delta_s": 0.9,
        "result": "tolerable",
    }
    assert decoded_duration_check(doc) == "tolerable"


@pytest.mark.parametrize(
    "check", [None, "ok", {}, {"result": "rejected"}, {"result": 1}, {"result": "OK"}]
)
def test_a_present_but_unreadable_decoded_check_is_malformed_and_refused(check: object) -> None:
    doc = copy.deepcopy(FULL)
    doc["provenance"]["source"]["decoded_duration_check"] = check
    assert decoded_duration_check(doc) == MALFORMED
    seen = analysed(doc, None)
    assert seen is not None
    match = RecordingMatch("confident", 0.91, "ok", decoded_duration_check(doc))
    decision = decide(TimingInputs(SPOTIFY, seen, match, alignment(doc)))
    assert (decision.timing_class, decision.reason) == ("unusable", "duration_rejected")


def test_the_record_holds_the_planned_fields() -> None:
    decision = youtube_decision(FULL)
    kept = provenance_of(FULL, decision)
    assert kept is not None
    record = kept.record()
    assert record == {
        "record_version": 1,
        "analysis_id": "an_01example",
        "analyzer": {"name": "spotdown", "version": "1.0.0", "config_hash": "b" * 64},
        "audio": {
            "sha256": SHA,
            "fingerprint": "fp-example",
            "codec": "mp3",
            "sample_rate_hz": 44100.0,
            "channels": 2.0,
            "file_duration_s": 12.0,
            "content_duration_s": 11.1,
        },
        "source": {"kind": "youtube", "ref": "dQw4w9WgXcQ"},
        "timing": {
            "class": "approximate",
            "reason": "matched_recording",
            "evidence": dict(decision.evidence),
        },
        "policy_versions": {
            "spotdown_map_policy": spotdown_map_policy.POLICY_VERSION,
            "timing_class_policy": timing_class_policy.POLICY_VERSION,
            "track_fit_policy": track_fit_policy.POLICY_VERSION,
        },
    }
    json.dumps(record, allow_nan=False)  # a plain JSON object


def test_the_record_keeps_no_title_query_scope_or_client_ref() -> None:
    doc = copy.deepcopy(FULL)
    doc["client_ref"] = "track-7-PLANTED"
    doc["provenance"]["run"]["job_id"] = "job_PLANTED"
    kept = provenance_of(doc, youtube_decision(doc))
    assert kept is not None
    text = json.dumps(kept.record())
    for planted in ("example artist", "example title", "PLANTED", "zzzzzzzzzzz", "librosa"):
        assert planted not in text


def test_an_upload_keeps_no_source_ref() -> None:
    doc = copy.deepcopy(MINIMAL)
    doc["provenance"]["source"]["ref"] = "song.mp3"  # never sent; never kept either
    seen = analysed(doc, ("swedebeat_track", STUDIO.ref))
    assert seen is not None
    kept = provenance_of(doc, decide(TimingInputs(STUDIO, seen)))
    assert kept is not None
    assert kept.record()["source"] == {"kind": "upload", "ref": None}


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("provenance", "audio", "sha256"), "A" * 64),
        (("provenance", "audio", "sha256"), "a" * 63),
        (("provenance", "audio"), None),
        (("provenance", "source", "kind"), "spotify"),
        (("provenance", "analyzer", "analyzer_version"), 1),
        (("analysis_id",), ""),
    ],
)  # the record needs its identity
def test_a_provenance_without_its_identity_is_not_kept(path: tuple[str, ...], value: Any) -> None:
    doc = copy.deepcopy(FULL)
    node = doc
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    decision = timing_class_policy.TimingDecision("unusable", "no_alignment", {})
    assert provenance_of(doc, decision) is None


@pytest.mark.parametrize(
    "ref", ["dQw4w9WgXc", "dQw4w9WgXcQQ", "dQw4w9WgXc/", "https://youtu.be/dQw4w9WgXcQ", 7, None]
)
def test_only_a_youtube_video_id_is_kept_as_the_source_ref(ref: object) -> None:
    doc = copy.deepcopy(FULL)
    doc["provenance"]["source"]["ref"] = ref
    kept = provenance_of(doc, youtube_decision(doc))
    assert kept is not None
    assert kept.record()["source"] == {"kind": "youtube", "ref": None}


def test_the_policy_versions_are_the_modules_own() -> None:
    assert dict(POLICY_VERSIONS) == {
        "spotdown_map_policy": "1",
        "timing_class_policy": "1",
        "track_fit_policy": "1",
    }


# --- reuse: never on the fingerprint alone -----------------------------------------------


HELD = audio_identity(FULL)


@pytest.mark.parametrize(
    ("sha256", "version", "link", "reused"),
    [
        (SHA, "1.0.0", False, True),
        (None, "1.0.0", True, True),  # the source-reference link
        ("c" * 64, "1.0.0", False, False),  # another file, even with the same fingerprint
        (None, "1.0.0", False, False),  # nothing but the fingerprint
        (SHA, "2.0.0", False, False),  # another analyser version
        (None, "2.0.0", True, False),
    ],
)
def test_reuse_needs_an_equal_sha256_or_a_source_link(
    sha256: str | None, version: str, link: bool, reused: bool
) -> None:
    assert isinstance(HELD, AudioIdentity)
    got = may_reuse(HELD, "1.0.0", sha256=sha256, analyzer_version=version, source_link=link)
    assert got is reused


def test_a_fingerprint_only_match_never_reuses_an_analysis() -> None:
    assert isinstance(HELD, AudioIdentity)
    twin = AudioIdentity("d" * 64, HELD.fingerprint, "aac", 48000.0, 2.0, 12.0, 11.1)
    assert twin.fingerprint == HELD.fingerprint
    assert not may_reuse(HELD, "1.0.0", sha256=twin.sha256, analyzer_version="1.0.0")
