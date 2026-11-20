"""SD-P5 (#1302, ADR 0040 sections 3, 6, 8 and 9): the pure rules of studio tracks analysed by
spotdown: the map always through ``timing_class_policy`` (path (a) only) and
``spotdown_map_policy`` (``studio_map_model``), the provenance kept with it and the cache's rule
for a stored one, the failure reactions and the budget (``studio_policy``), the allowlist setting
(``studio_owners_policy``) and when spotdown is the source (``studio_tracks_config``). No
database, no network."""

import copy
import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from classbeat import egress_spotdown
from classbeat.config import Settings
from classbeat.spotdown import studio_policy as sp
from classbeat.spotdown.failure_policy import TrackFailure, Unreachable
from classbeat.spotdown.provenance_model import POLICY_VERSIONS
from classbeat.spotdown.studio_map_model import (
    StudioMap,
    StudioRefused,
    current_provenance,
    studio_map,
)
from classbeat.spotdown.studio_owners_policy import (
    MAX_OWNERS,
    StudioOwnersRefused,
    canonical,
    parse_owners,
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
    assert record["policy_versions"] == dict(POLICY_VERSIONS)
    assert record["source"] == {"kind": "upload", "ref": None}
    assert REFERENCE not in json.dumps(record)  # the played reference is never kept
    assert current_provenance(record, SHA)
    assert not current_provenance(record, "d" * 64)  # the record of other bytes than the row's


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
        (Unreachable("connection"), False, "outage"),
        (Unreachable("server_error"), False, "outage"),
        (Unreachable("unauthorized"), True, "outage"),
        (TrackFailure("rate_limited", retry_after_s=30.0), True, "wait"),
        (TrackFailure("provider_error"), False, "retry"),
        (TrackFailure("provider_error"), True, "fallback"),
        (TrackFailure("processing_failed", failure_kind="timeout"), False, "retry"),
        (TrackFailure("processing_failed", failure_kind="timeout"), True, "fallback"),
        (TrackFailure("processing_failed", failure_kind="decode_error"), False, "fallback"),
        (TrackFailure("processing_failed", failure_kind="out_of_memory"), False, "fallback"),
        (TrackFailure("alignment_failed", analysis_id="an_1"), False, "fallback"),
        (TrackFailure("not_found"), False, "fallback"),
        (TrackFailure("uncertain"), False, "fallback"),
        (TrackFailure("source_unavailable"), False, "fallback"),
    ],
)
def test_each_failure_has_its_bounded_reaction(
    failure: TrackFailure | Unreachable, retried: bool, step: str
) -> None:
    assert sp.reaction(failure, retried) == step


@pytest.mark.parametrize(
    ("errors", "tried", "outage"),
    [(0, 0, False), (2, 2, False), (3, 3, True), (3, 6, True), (3, 7, False), (4, 8, True)],
)
def test_a_provider_outage_needs_three_tracks_and_half_the_run(
    errors: int, tried: int, outage: bool
) -> None:
    assert sp.provider_outage(errors, tried) is outage


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


# --- the allowlist setting ----------------------------------------------------------------------


def test_the_owners_are_canonical_sorted_and_empty_is_nobody() -> None:
    a, b = "0B6F1A52-6F0E-4F43-9A51-6C1D2A0C9E11", "00000000-0000-4000-8000-000000000001"
    assert canonical(f" {a} , {b} ") == f"{b},{a.lower()}"
    assert parse_owners("") == frozenset()
    assert parse_owners("  ") == frozenset()
    assert parse_owners(a) == {OWNER}


@pytest.mark.parametrize(
    "value",
    [
        "not-a-uuid",
        "0b6f1a526f0e4f439a516c1d2a0c9e11",  # only the dashed form
        "{0b6f1a52-6f0e-4f43-9a51-6c1d2a0c9e11}",
        "urn:uuid:0b6f1a52-6f0e-4f43-9a51-6c1d2a0c9e11",
        "0b6f1a52-6f0e-4f43-9a51-6c1d2a0c9e11,0b6f1a52-6f0e-4f43-9a51-6c1d2a0c9e11",
        "0b6f1a52-6f0e-4f43-9a51-6c1d2a0c9e11,",
        ",".join(str(uuid4()) for _ in range(MAX_OWNERS + 1)),
    ],
)
def test_a_bad_allowlist_is_refused_without_the_value(value: str) -> None:
    with pytest.raises(StudioOwnersRefused) as refused:
        parse_owners(value)
    assert value not in str(refused.value)


def test_the_settings_default_to_off_and_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CLASSBEAT_SPOTDOWN_STUDIO", "CLASSBEAT_SPOTDOWN_STUDIO_OWNERS"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (settings.spotdown_studio, settings.spotdown_studio_owners) == ("off", "")


@pytest.mark.parametrize("value", ["on", "default", "true", "everyone"])
def test_no_setting_switches_it_on_for_everyone(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_STUDIO", value)
    with pytest.raises(ValueError, match="spotdown_studio"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_the_allowlist_setting_is_read_and_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_STUDIO", "allowlist")
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_STUDIO_OWNERS", str(OWNER).upper())
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (settings.spotdown_studio, settings.spotdown_studio_owners) == ("allowlist", str(OWNER))
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_STUDIO_OWNERS", "nobody")
    with pytest.raises(ValueError, match="spotdown_studio_owners must be"):
        Settings(_env_file=None)  # type: ignore[call-arg]


# --- when spotdown is the source ----------------------------------------------------------------

KEY = bytes(range(32))
ON = TracksConfig("allowlist", frozenset({OWNER}), frozenset({"upload"}), KEY)


@pytest.fixture
def installed() -> Any:
    from pydantic import SecretStr

    egress_spotdown.install_spotdown("http://spotdown-api:8000", SecretStr(SERVICE_KEY))
    try:
        yield
    finally:
        egress_spotdown.install_spotdown(None, None)


def test_only_an_allowlisted_owner_with_everything_set(installed: None) -> None:
    assert ON.active_for(OWNER)
    assert not ON.active_for(uuid4())  # not on the list
    assert not TracksConfig().active_for(OWNER)  # the default
    assert not TracksConfig("off", ON.owners, ON.sources, KEY).active_for(OWNER)
    assert not TracksConfig("allowlist", ON.owners, frozenset({"youtube"}), KEY).active_for(OWNER)
    assert not TracksConfig("allowlist", ON.owners, ON.sources, None).active_for(OWNER)
    assert "scope_key" not in repr(ON)
    assert str(KEY) not in repr(ON)


def test_not_without_spotdown_installed() -> None:
    assert not ON.active_for(OWNER)


def test_the_budget_left_is_the_jobs_from_its_first_spotdown_call() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    assert sp.remaining(now, now) == 600.0
    assert sp.remaining(now - timedelta(seconds=90), now) == 510.0
    assert sp.remaining(now - timedelta(minutes=11), now) == 0.0
    assert sp.remaining(now + timedelta(minutes=5), now) == 600.0  # a clock step never adds


def test_a_record_wants_exactly_its_analyser_and_bytes() -> None:
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
    assert wanted(legacy, ["a"]) == {"a": Wanted("local", None)}
    music = StudioRevisionMusic.model_validate(
        {**base, "mix_spec": spec, "analyses": [
            {"reference": "a", "analyser": "spotdown", "sha256": SHA,
             "map_hash": map_hash({"m": 1}), "provenance": {}}]},
        strict=False,
    )  # fmt: skip
    want = wanted(music, ["a", "b"])
    assert want == {"a": Wanted("spotdown", SHA, map_hash({"m": 1}))}  # "b" has no record
    accept = recorded(want)
    assert accept(Stored("a", "spotdown", SHA, {}, {"m": 1}))
    assert not accept(Stored("a", "local", SHA, None, {"m": 1}))  # never the other analyser
    assert not accept(Stored("a", "spotdown", "d" * 64, {}, {"m": 1}))  # nor other bytes
    assert not accept(Stored("a", "spotdown", SHA, {}, {"m": 2}))  # nor another map of them
    assert not accept(Stored("b", "local", SHA, None, {"m": 1}))
    assert recorded(wanted(legacy, ["a"]))(Stored("a", "local", "d" * 64, None, {"m": 3}))
