"""Explicit lyrics per track (ADR 0040 section 6, #1340): ``spotdown.explicit_model`` and the
request builders' ``explicit`` field, as table tests. Pure: no database, no network.

Report only: a song shows the tag when Spotify's flag or spotdown's own ``content.explicit`` says
``yes``; ``no``, ``unknown`` and nothing at all show nothing; a value of the wrong type is no
evidence; the request carries the flag only when it is known.
"""

import copy
import json
from typing import Any

import pytest

from classbeat.db.role_check import DatabaseRoleError
from classbeat.jobs.errors import LeaseLost, RunStopped
from classbeat.songs import pick_explicit
from classbeat.songs.pick_explicit import explicit_report
from classbeat.spotdown import explicit_model as em
from classbeat.spotdown.provenance_read_calc import AudioIdentity
from classbeat.spotdown.request_model import TrackQuery, analysis_body, match_body
from classbeat.spotdown.song_analysis_record_model import (
    Analysed,
    SongOutcome,
    analysis_record,
)
from classbeat.spotdown.timing_class_policy import TimingDecision
from spotdownfake import example, explicit_group

YES = explicit_group([{"source": "caller", "value": True}])
NO = explicit_group([{"source": "caller", "value": False}])
UNKNOWN = explicit_group([])
KEPT = {"value": "yes", "confidence": 0.9, "evidence": [{"source": "caller", "value": True}]}


def group(value: str) -> dict[str, Any]:
    """The group as the record keeps it (no ``method``)."""
    g = {"yes": YES, "no": NO, "unknown": UNKNOWN}[value]
    return {k: v for k, v in g.items() if k != "method"}


# --- the request carries the flag only when it is known -----------------------------------------


@pytest.mark.parametrize(
    ("flag", "sent"),
    [(True, True), (False, False), (None, "absent")],
)
def test_a_match_item_carries_explicit_only_when_known(flag: bool | None, sent: object) -> None:
    track = TrackQuery("t0", "Song", ("Artist",), 200.0, None, flag)
    item = json.loads(match_body((track,), None))["tracks"][0]
    assert item.get("explicit", "absent") == sent


@pytest.mark.parametrize(
    ("flag", "sent"),
    [(True, True), (False, False), (None, "absent")],
)
def test_an_analysis_request_carries_explicit_only_when_known(
    flag: bool | None, sent: object
) -> None:
    body = json.loads(analysis_body(None, "p0", source_ref="youtube:abcdefghijk", explicit=flag))
    assert body.get("explicit", "absent") == sent


@pytest.mark.parametrize("bad", [1, 0, "true", "yes", [], {}])
def test_a_flag_that_is_not_a_boolean_is_left_out_and_never_fails_the_request(bad: Any) -> None:
    track = TrackQuery("t0", "Song", ("Artist",), 200.0, None, bad)
    assert "explicit" not in json.loads(match_body((track,), None))["tracks"][0]
    body = analysis_body(None, "p0", source_ref="youtube:abcdefghijk", explicit=bad)
    assert "explicit" not in json.loads(body)


# --- spotdown's own group, read strictly --------------------------------------------------------


def test_the_documents_group_is_read_and_an_older_document_has_none() -> None:
    doc = example("analysis-v1.full.json")
    assert em.document_group(doc) == {
        "value": "yes",
        "confidence": 0.5,
        "evidence": [
            {"source": "caller", "value": False},
            {"source": "youtube_age_limit", "value": 18},
        ],
    }
    assert em.document_group(example("analysis-v1.minimal.json")) is None  # absent, not unknown


def _broken(change: Any) -> dict[str, Any]:
    made = copy.deepcopy(group("yes"))
    change(made)
    return made


BROKEN = [
    None,
    "yes",
    {},
    _broken(lambda g: g.update(value="maybe")),
    _broken(lambda g: g.update(value=True)),
    _broken(lambda g: g.update(confidence=1.5)),
    _broken(lambda g: g.update(confidence=True)),
    _broken(lambda g: g.update(confidence="0.9")),
    _broken(lambda g: g.update(method="metadata")),  # not a stored key
    _broken(lambda g: g.update(extra=1)),
    _broken(lambda g: g.pop("evidence")),
    _broken(lambda g: g.update(evidence=None)),
    _broken(lambda g: g.update(evidence=[{"source": "caller", "value": True}] * 4)),
    _broken(lambda g: g.update(evidence=[{"source": "caller", "value": "true"}])),
    _broken(lambda g: g.update(evidence=[{"source": "youtube_age_limit", "value": True}])),
    _broken(lambda g: g.update(evidence=[{"source": "youtube_age_limit", "value": 0}])),
    _broken(lambda g: g.update(evidence=[{"source": "somewhere", "value": True}])),
    _broken(lambda g: g.update(evidence=[{"source": "caller", "value": True, "extra": 1}])),
    _broken(lambda g: g.update(evidence=["caller"])),
    _broken(lambda g: g.update(evidence=[{"source": "youtube_age_limit", "value": 17}])),  # < 18
    # unhashable values: a type check comes before every set lookup (#1344 review)
    _broken(lambda g: g.update(value=[])),
    _broken(lambda g: g.update(value={"a": 1})),
    _broken(lambda g: g.update(confidence=[])),
    _broken(lambda g: g.update(evidence=[{"source": [], "value": True}])),
    _broken(lambda g: g.update(evidence=[{"source": {"a": 1}, "value": True}])),
    _broken(lambda g: g.update(evidence=[{"source": "caller", "value": []}])),
    _broken(lambda g: g.update(evidence=[{"source": "youtube_age_limit", "value": {}}])),
]


@pytest.mark.parametrize("broken", BROKEN)
def test_a_group_of_the_wrong_shape_is_not_evidence(broken: Any) -> None:
    assert em.spotdown_group(broken) is None


@pytest.mark.parametrize("broken", [b for b in BROKEN if isinstance(b, dict) and b])
def test_a_document_whose_group_is_of_the_wrong_shape_reports_none(broken: Any) -> None:
    held = {k: v for k, v in broken.items() if k != "method"}
    for method in ("metadata", "audio"):  # the schema's one method; a group without it is no group
        doc = {"content": {"explicit": {**held, "method": method}}}
        expected = None if method != "metadata" or em.spotdown_group(held) is None else held
        assert em.document_group(doc) == expected
    assert em.document_group({"content": {"explicit": {**held}}}) is None  # no method at all


# --- what shows the tag -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spotify", "spotdown", "shown"),
    [
        (True, None, True),
        (False, None, False),
        (None, None, False),
        (False, "yes", True),  # either source: a caution flag errs toward flagging
        (True, "no", True),
        (False, "no", False),
        (None, "unknown", False),
        (False, "unknown", False),
        (None, "yes", True),
    ],
)
def test_a_song_shows_the_tag_when_either_source_says_yes(
    spotify: bool | None, spotdown: str | None, shown: bool
) -> None:
    ev = em.Evidence(spotify, None if spotdown is None else group(spotdown))
    assert ev.shown() is shown


def test_the_record_keeps_each_source_apart_and_reads_back_the_same() -> None:
    found = {
        0: em.Evidence(True, None),
        1: em.Evidence(False, group("no")),
        2: em.Evidence(None, group("unknown")),
        3: em.Evidence(),  # nothing known: not recorded
        4: em.Evidence(None, group("yes")),
    }
    made = em.record(found)
    assert made[em.KEY]["v"] == 1
    assert [s["position"] for s in made[em.KEY]["songs"]] == [0, 1, 2, 4]
    assert made[em.KEY]["songs"][0] == {"position": 0, "spotify": True}
    assert em.evidence_of(made) == {p: ev for p, ev in found.items() if ev.known()}
    assert em.shown_positions(made) == frozenset({0, 4})


def test_a_list_without_any_evidence_records_nothing() -> None:
    assert em.record({}) == {}
    assert em.record({0: em.Evidence(), 1: em.Evidence()}) == {}
    assert em.shown_positions({}) == frozenset()


@pytest.mark.parametrize(
    "stored",
    [
        {},
        {em.KEY: None},
        {em.KEY: {"songs": "x"}},
        {em.KEY: {"songs": [None, 3, "a", {"position": "0", "spotify": True}]}},
        {em.KEY: {"songs": [{"position": True, "spotify": True}]}},
        {em.KEY: {"songs": [{"position": 0, "spotify": 1}]}},
        {em.KEY: {"songs": [{"position": 0, "spotify": "true"}]}},
        {em.KEY: {"songs": [{"position": 0, "spotdown": {"value": "yes"}}]}},
    ],
)
def test_a_stored_record_of_the_wrong_shape_shows_nothing(stored: dict[str, Any]) -> None:
    assert em.evidence_of(stored) == {}
    assert em.shown_positions(stored) == frozenset()


def test_the_first_entry_of_a_position_stands() -> None:
    stored = {
        em.KEY: {"songs": [{"position": 0, "spotify": False}, {"position": 0, "spotify": True}]}
    }
    assert em.evidence_of(stored) == {0: em.Evidence(False, None)}


# --- the evidence of a new list -----------------------------------------------------------------


def test_new_songs_have_spotifys_flag_and_their_analysis_group_and_kept_ones_the_parents() -> None:
    parent = {2: em.Evidence(True, None), 3: em.Evidence(False, KEPT)}
    listed = [("a", True), ("b", True), ("k2", False), ("k3", False), ("c", True)]
    found = em.evidence_for(
        listed,
        {"a": True, "b": False},  # "c": Spotify sent no flag
        {1: group("yes"), 4: group("no")},
        parent,
        [(2, "k2"), (3, "k3")],
    )
    assert found == {
        0: em.Evidence(True, None),
        1: em.Evidence(False, group("yes")),
        2: em.Evidence(True, None),
        3: em.Evidence(False, KEPT),
        4: em.Evidence(None, group("no")),
    }


def test_a_kept_song_carries_evidence_only_from_its_own_slot() -> None:
    parent = {0: em.Evidence(True, None)}
    # the song at slot 0 is not the kept one, or is new: the parent's slot says nothing about it
    assert em.evidence_for([("x", False)], {}, {}, parent, [(0, "other")]) == {}
    assert em.evidence_for([("x", True)], {}, {}, parent, [(0, "x")]) == {0: em.Evidence()}
    assert em.evidence_for([("x", False)], {}, {}, parent, [(5, "x")]) == {}
    assert em.evidence_for([("x", False)], {}, {}, {}, [(0, "x")]) == {}


def test_spotdowns_group_of_each_analysed_song_comes_from_the_analysis_record() -> None:
    audio = AudioIdentity("f" * 64, "fp", "mp3", 44100, 2, 240.0, 240.0)
    decision = TimingDecision("approximate", "matched_recording", {}, "1")
    seen = Analysed("an_1", ("analyzer", "1", None), audio, decision, group("yes"))
    plain = Analysed("an_2", ("analyzer", "1", None), audio, decision)
    unusable = Analysed("an_3", ("analyzer", "1", None), audio, decision, group("yes"))
    record = analysis_record(
        {
            0: SongOutcome("spotdown", None, seen),
            1: SongOutcome("spotdown", None, plain),
            2: SongOutcome("sections", "bad_document", unusable),  # not spotdown's timing
        },
        {},
        rounds=0,
    )
    assert em.analysis_groups(record) == {0: group("yes")}


def test_song_and_songout_leave_the_flag_out_unless_true() -> None:
    from classbeat.classes.song_list_out import SongOut
    from classbeat.songs.song_list_model import Song

    base: dict[str, Any] = {
        "spotify_id": "a" * 22, "title": "T", "artist": "A", "role": "warmup", "bpm": 120.0,
        "length_sec": 200, "new": False,
    }  # fmt: skip
    assert "explicit" not in Song(**base).model_dump(mode="json", exclude_none=False)
    assert Song(**base, explicit=True).model_dump(mode="json")["explicit"] is True
    out: dict[str, Any] = {
        k: base[k] for k in ("title", "artist", "role", "bpm", "length_sec", "new")
    }
    assert "explicit" not in SongOut(position=1, **out).model_dump(mode="json")
    assert SongOut(position=1, explicit=True, **out).model_dump(mode="json")["explicit"] is True


@pytest.mark.parametrize("bad", [[], {}, [1], {"a": []}, None, 1, "yes", True])
def test_document_group_is_total(bad: Any) -> None:
    """Never raises, whatever ``content.explicit`` holds (it is called outside the swallow)."""
    for field in ("value", "confidence", "method", "evidence"):
        group = {"value": "yes", "confidence": 0.9, "method": "metadata", "evidence": []}
        group[field] = bad
        em.document_group({"content": {"explicit": group}})  # must not raise
    assert em.document_group({"content": {"explicit": bad}}) is None
    assert em.document_group({"content": bad}) is None


# The swallow of the report (no database): only the run-stopping errors pass.
@pytest.mark.parametrize("error", [RunStopped("cancelled"), LeaseLost(), DatabaseRoleError("x")])
def test_the_run_stopping_errors_pass_through(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise error

    monkeypatch.setattr(em, "evidence_for", boom)
    matched = type("M", (), {"playlist": []})()
    chosen = type("C", (), {"songs": []})()
    with pytest.raises(type(error)):
        explicit_report(matched, chosen, {}, None)  # type: ignore[arg-type]
    assert (RunStopped, LeaseLost, DatabaseRoleError) == pick_explicit.PASSED


def test_any_other_error_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("planted")

    monkeypatch.setattr(em, "evidence_for", boom)
    matched = type("M", (), {"playlist": []})()
    chosen = type("C", (), {"songs": []})()
    assert explicit_report(matched, chosen, {}, None) == ({}, frozenset())  # type: ignore[arg-type]
