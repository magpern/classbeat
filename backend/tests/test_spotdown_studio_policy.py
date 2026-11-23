"""SD-P5 (#1302, ADR 0040 sections 3, 6, 8 and 9): the pure rules of studio tracks analysed by
spotdown: the map always through ``timing_class_policy`` (path (a) only) and
``spotdown_map_policy`` (``studio_map_model``), the provenance kept with it and the cache's rule
for a stored one, the failure reactions and the budget (``studio_policy``) and when spotdown is
usable (``studio_tracks_config``; the four settings are gone, #1353). No database, no network."""

import copy
import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from classbeat import egress_spotdown
from classbeat.config import Settings
from classbeat.spotdown import studio_policy as sp
from classbeat.spotdown.failure_policy import TrackFailure
from classbeat.spotdown.studio_map_model import (
    STUDIO_POLICY_VERSIONS,
    StudioMap,
    StudioRefused,
    current_provenance,
    studio_map,
)
from classbeat.spotdown.studio_tracks_config import TracksConfig
from spotdownfake import SERVICE_KEY, example

SHA = "c" * 64
REFERENCE = "trk-secret-ref-01"
OWNER = UUID("0b6f1a52-6f0e-4f43-9a51-6c1d2a0c9e11")


def doc(sha: str = SHA, kind: str = "upload") -> dict[str, Any]:
    document = copy.deepcopy(example("analysis-v1.minimal.json"))
    document["provenance"]["audio"]["sha256"] = sha
    document["provenance"]["source"]["kind"] = kind
    return document


# --- the map: always through the two policies --------------------------------------------------


def test_the_played_bytes_are_exact_and_mapped_with_their_provenance() -> None:
    mapped = studio_map(doc(), REFERENCE, SHA, 1)
    assert isinstance(mapped, StudioMap)
    assert mapped.track_map["track_version"] == 1
    assert mapped.track_map["songs"][0]["bpm"] == pytest.approx(128.0)  # type: ignore[index]
    record = mapped.provenance
    assert record["timing"]["class"] == "exact"  # type: ignore[index]
    assert record["timing"]["reason"] == "played_recording"  # type: ignore[index]
    assert record["audio"]["sha256"] == SHA  # type: ignore[index]
    assert record["policy_versions"] == dict(STUDIO_POLICY_VERSIONS)
    assert record["source"] == {"kind": "upload", "ref": None}
    assert REFERENCE not in json.dumps(record)  # the played reference is never kept
    assert current_provenance(record, SHA)
    assert not current_provenance(record, "d" * 64)  # the record of other bytes than the row's


def test_the_map_carries_its_candidates_within_the_tables_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from classbeat.design.track_map_policy import MAX_MAP_BYTES
    from classbeat.jsonb_text_calc import jsonb_text_size
    from classbeat.spotdown import studio_map_model

    assert studio_map_model.MAP_MAX == MAX_MAP_BYTES  # the same check as ``valid_track_map``
    mapped = studio_map(doc(), REFERENCE, SHA, 1)
    assert isinstance(mapped, StudioMap)
    assert mapped.track_map["cut_candidates"]  # #1328
    size = jsonb_text_size(mapped.track_map)
    monkeypatch.setattr(studio_map_model, "MAP_MAX", size)
    assert isinstance(studio_map(doc(), REFERENCE, SHA, 1), StudioMap)  # at the limit: stored
    monkeypatch.setattr(studio_map_model, "MAP_MAX", size - 1)
    assert studio_map(doc(), REFERENCE, SHA, 1) == StudioRefused("map_too_large")


def test_a_map_the_table_could_not_measure_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from classbeat.spotdown import studio_map_model

    def unmeasurable(_value: object) -> int:
        raise TypeError("not JSON")

    monkeypatch.setattr(studio_map_model, "jsonb_text_size", unmeasurable)
    assert studio_map(doc(), REFERENCE, SHA, 1) == StudioRefused("map_too_large")


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        (doc(sha="d" * 64), "not_played_recording"),  # other bytes: never exact
        (doc(kind="youtube"), "not_played_recording"),  # not classbeat's upload
        (doc(kind="url"), "not_played_recording"),
    ],
)
def test_anything_but_the_played_upload_is_refused(document: dict[str, Any], reason: str) -> None:
    assert studio_map(document, REFERENCE, SHA, 1) == StudioRefused(reason)


def test_a_document_without_an_audio_identity_or_provenance_is_refused() -> None:
    no_audio = doc()
    del no_audio["provenance"]["audio"]
    assert studio_map(no_audio, REFERENCE, SHA, 1) == StudioRefused("no_audio_identity")
    no_analyzer = doc()
    del no_analyzer["provenance"]["analyzer"]
    assert studio_map(no_analyzer, REFERENCE, SHA, 1) == StudioRefused("no_provenance")


def test_what_the_map_policy_refuses_is_refused_with_its_code() -> None:
    no_beats = doc()
    del no_beats["grid"]
    assert studio_map(no_beats, REFERENCE, SHA, 1) == StudioRefused("no_beats")
    no_tempo = doc()
    no_tempo["tempo"]["confidence"] = 0.0
    assert studio_map(no_tempo, REFERENCE, SHA, 1) == StudioRefused("bpm_untrusted")


@pytest.mark.parametrize(
    ("timing_class", "reason"),
    [("exact", "beat_equivalence"), ("approximate", "matched_recording"), ("unusable", "x")],
)
def test_only_a_played_recording_decision_is_used_whatever_the_map_policy_says(
    monkeypatch: pytest.MonkeyPatch, timing_class: str, reason: str
) -> None:
    """studio_map's own check, before ``spotdown_map_policy`` (which refuses with its own code)."""
    from classbeat.spotdown import timing_class_policy

    def decided(_inputs: object) -> Any:
        return timing_class_policy.TimingDecision(timing_class, reason, {})  # type: ignore[arg-type]

    monkeypatch.setattr(timing_class_policy, "decide", decided)
    assert studio_map(doc(), REFERENCE, SHA, 1) == StudioRefused("not_played_recording")


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(record_version=2),
        lambda r: r["timing"].update({"class": "approximate"}),
        lambda r: r["timing"].update(reason="beat_equivalence"),
        lambda r: r["policy_versions"].update(spotdown_map_policy="0"),
        lambda r: r.pop("policy_versions"),
    ],
)
def test_a_stored_provenance_of_another_build_is_not_current(change: Any) -> None:
    mapped = studio_map(doc(), REFERENCE, SHA, 1)
    assert isinstance(mapped, StudioMap)
    record = copy.deepcopy(mapped.provenance)
    change(record)
    assert not current_provenance(record, SHA)
    assert not current_provenance(None, SHA)
    assert not current_provenance("exact", SHA)


# --- the reactions of section 8 ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "retried", "step"),
    [
        (TrackFailure("rate_limited", retry_after_s=30.0), True, "wait"),
        (TrackFailure("provider_error"), False, "retry"),
        (TrackFailure("provider_error"), True, "unavailable"),  # retried, with the job's backoff
        (TrackFailure("processing_failed", failure_kind="timeout"), False, "retry"),
        (TrackFailure("processing_failed", failure_kind="timeout"), True, "failed"),
        (TrackFailure("processing_failed", failure_kind="decode_error"), False, "failed"),
        (TrackFailure("processing_failed", failure_kind="out_of_memory"), False, "failed"),
        (TrackFailure("alignment_failed", analysis_id="an_1"), False, "failed"),
        (TrackFailure("not_found"), False, "failed"),
        (TrackFailure("uncertain"), False, "failed"),
        (TrackFailure("source_unavailable"), False, "failed"),
    ],
)
def test_each_failure_has_its_bounded_reaction(
    failure: TrackFailure, retried: bool, step: str
) -> None:
    assert sp.reaction(failure, retried) == step


@pytest.mark.parametrize(
    ("retry_after", "left", "wait"),
    [(30.0, 600.0, 30.0), (None, 600.0, 0.0), (600.0, 600.0, None), (45.0, 10.0, None)],
)
def test_a_rate_limited_wait_must_fit_the_budget(
    retry_after: float | None, left: float, wait: float | None
) -> None:
    assert sp.wait_for(retry_after, left) == wait


def test_the_budget_is_about_ten_minutes() -> None:
    assert sp.WAIT_SECONDS == 600.0


# --- when spotdown is usable ---------------------------------------------------------------------

KEY = bytes(range(32))
ON = TracksConfig(KEY)


@pytest.fixture
def installed() -> Any:
    from pydantic import SecretStr

    egress_spotdown.install_spotdown("http://spotdown-api:8000", SecretStr(SERVICE_KEY))
    try:
        yield
    finally:
        egress_spotdown.install_spotdown(None, None)


def test_usable_only_with_everything_set_else_the_refusal_names_the_unreachable_reason(
    installed: None,
) -> None:
    assert ON.refusal() is None
    assert TracksConfig().refusal() == "not_configured"  # the default: nothing set
    assert TracksConfig(None).refusal() == "not_configured"  # no scope key
    assert "scope_key" not in repr(ON)
    assert str(KEY) not in repr(ON)


def test_not_usable_without_spotdown_installed() -> None:
    assert ON.refusal() == "not_configured"


@pytest.mark.parametrize("name", ["SPOTDOWN_STUDIO", "SPOTDOWN_STUDIO_OWNERS", "SPOTDOWN_MIX"])
def test_the_removed_settings_are_ignored_as_any_unknown_one(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(f"CLASSBEAT_{name}", "allowlist")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    for field in (
        "spotdown_studio",
        "spotdown_studio_owners",
        "spotdown_mix",
        "spotdown_mix_owners",
    ):
        assert not hasattr(settings, field)


def test_the_budget_left_is_the_jobs_from_its_first_spotdown_call() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    assert sp.remaining(now, now) == 600.0
    assert sp.remaining(now - timedelta(seconds=90), now) == 510.0
    assert sp.remaining(now - timedelta(minutes=11), now) == 0.0
    assert sp.remaining(now + timedelta(minutes=5), now) == 600.0  # a clock step never adds


def test_a_record_wants_exactly_its_bytes_and_map() -> None:
    from classbeat.design.studio_revision_model import (
        StudioRevisionMusic,
        Wanted,
        map_hash,
        wanted,
    )
    from classbeat.design.track_cache import Stored, recorded

    spec = {"tracks": [{"reference": "a", "start_sec": 0.0, "end_sec": 60.0}], "crossfades": []}
    base = {"source": "swedebeat_studio", "song_list_id": str(uuid4()), "map_version": 1}
    legacy = StudioRevisionMusic.model_validate({**base, "mix_spec": spec}, strict=False)
    assert wanted(legacy, ["a"]) == {}  # #1356: no local row to read; the music changed
    music = StudioRevisionMusic.model_validate(
        {**base, "mix_spec": spec, "analyses": [
            {"reference": "a", "sha256": SHA, "map_hash": map_hash({"m": 1}), "provenance": {}}]},
        strict=False,
    )  # fmt: skip
    want = wanted(music, ["a", "b"])
    assert want == {"a": Wanted(SHA, map_hash({"m": 1}))}  # "b" has no record
    accept = recorded(want)
    assert accept(Stored("a", SHA, {}, {"m": 1}))
    assert not accept(Stored("a", "d" * 64, {}, {"m": 1}))  # never other bytes
    assert not accept(Stored("a", SHA, {}, {"m": 2}))  # nor another map of them
    assert not accept(Stored("b", SHA, {}, {"m": 1}))  # a reference with no record
    assert not recorded(wanted(legacy, ["a"]))(Stored("a", SHA, {}, {"m": 1}))
    with pytest.raises(ValidationError):  # a record of the retired local analyser is refused
        StudioRevisionMusic.model_validate(
            {**base, "mix_spec": spec, "analyses": [
                {"reference": "a", "analyser": "local", "sha256": SHA, "map_hash": SHA}]},
            strict=False,
        )  # fmt: skip


def test_a_draft_records_at_most_one_analysis_per_track_of_a_mix() -> None:
    """#1302 review: ``class_revisions.music`` carries at most ``MAX_TRACKS`` records."""
    from classbeat.design.studio_revision_model import StudioRevisionMusic
    from classbeat.design.track_map_policy import MAX_TRACKS

    spec = {"tracks": [{"reference": "a", "start_sec": 0.0, "end_sec": 60.0}], "crossfades": []}
    base = {"source": "swedebeat_studio", "song_list_id": str(uuid4()), "map_version": 1}
    record = {"reference": "a", "sha256": SHA, "map_hash": SHA}
    fits = {**base, "mix_spec": spec, "analyses": [record] * MAX_TRACKS}
    assert len(StudioRevisionMusic.model_validate(fits, strict=False).analyses or []) == MAX_TRACKS
    with pytest.raises(ValidationError):
        StudioRevisionMusic.model_validate({**fits, "analyses": [record] * (MAX_TRACKS + 1)})
