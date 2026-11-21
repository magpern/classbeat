"""The explicit report of a Spotify song pick (#1340, ADR 0040 section 6), as a job on the fakes.
Report only: the list is the same with or without a flag, apart from the ``explicit`` key of the
flagged songs and the provenance record ``explicit``. Never the network (netguard)."""

import json
from collections.abc import Callable
from typing import Any

import pytest

from classbeat.db.role_check import DatabaseRoleError
from classbeat.jobs.errors import LeaseLost, RunStopped
from classbeat.songs import pick_explicit
from classbeat.songs.pick_explicit import explicit_report
from classbeat.spotdown import explicit_model as model
from songtools import Rider, SongEnv, song_id
from spotdownfake import FakeSpotdown, spotdown
from test_spotdown_song_analysis_job import (
    SONGS,
    baseline,
    docs,
    listed,
    on,
    run_on,
    starts,
    vid,
    waits,
)

__all__ = ["on", "spotdown", "waits"]  # the fixture

pytestmark = pytest.mark.usefixtures("songenv")

KEY = model.KEY


def flag(songenv: SongEnv, n: int, value: bool) -> None:
    assert songenv.mowl.spotify is not None
    songenv.mowl.spotify.explicit[song_id(n)] = value


def tagged(revision: Any) -> list[int]:
    return [p for p, s in enumerate(revision.content["songs"]) if s.get("explicit") is True]


def bare(content: dict[str, Any]) -> dict[str, Any]:
    songs = [{k: v for k, v in s.items() if k != "explicit"} for s in content["songs"]]
    return {**content, "songs": songs}


async def test_without_a_flag_nothing_changes(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    _, revision = await listed(songenv, rider)
    assert tagged(revision) == []
    assert KEY not in revision.provenance
    assert all("explicit" not in s for s in revision.content["songs"])


async def test_spotifys_flag_tags_the_song_and_changes_nothing_else(songenv: SongEnv) -> None:
    today = await baseline(songenv)
    flag(songenv, SONGS[2], True)
    flag(songenv, SONGS[3], False)
    rider = await songenv.rider()
    _, revision = await listed(songenv, rider)
    assert tagged(revision) == [2]  # a "no" shows nothing
    assert bare(revision.content) == today.content  # same songs, same order
    record = revision.provenance[KEY]
    assert record["v"] == model.RECORD_VERSION
    assert {e["position"] for e in record["songs"]} == {2, 3}


async def test_spotdowns_yes_tags_a_song_spotify_does_not_flag(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    docs(spotdown, explicit="yes")
    _, revision = await run_on(songenv, on)
    assert tagged(revision) == list(range(8))
    assert all(e["spotdown"]["value"] == "yes" for e in revision.provenance[KEY]["songs"])


async def test_a_spotdown_no_does_not_hide_spotifys_yes(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    docs(spotdown, explicit="no")
    flag(songenv, SONGS[1], True)
    _, revision = await run_on(songenv, on)
    assert tagged(revision) == [1]


async def test_the_flag_is_sent_to_spotdown_only_when_known(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    flag(songenv, SONGS[0], True)
    flag(songenv, SONGS[1], False)
    await run_on(songenv, on)
    asked = starts(spotdown)
    by_source = {a["source_ref"]: a for a in asked}
    assert by_source[f"youtube:{vid(SONGS[0])}"]["explicit"] is True
    assert by_source[f"youtube:{vid(SONGS[1])}"]["explicit"] is False
    assert "explicit" not in by_source[f"youtube:{vid(SONGS[2])}"]
    matches = [json.loads(s.body) for s in spotdown.seen if s.path == "/v2/match"]
    items = [t for m in matches for t in m["tracks"]]
    assert any(t.get("explicit") is True for t in items)
    assert any("explicit" not in t for t in items)


async def test_a_failing_report_never_fails_the_pick(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = await baseline(songenv)
    flag(songenv, SONGS[2], True)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise ValueError("planted")

    monkeypatch.setattr(model, "evidence_for", boom)
    rider = await songenv.rider()
    _, revision = await listed(songenv, rider)
    assert revision.content == today.content
    assert KEY not in revision.provenance


@pytest.mark.parametrize("error", [RunStopped("cancelled"), LeaseLost(), DatabaseRoleError("x")])
def test_the_run_stopping_errors_pass_through(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise error

    monkeypatch.setattr(model, "evidence_for", boom)
    matched = type("M", (), {"playlist": []})()
    chosen = type("C", (), {"songs": []})()
    with pytest.raises(type(error)):
        explicit_report(matched, chosen, {}, None)  # type: ignore[arg-type]
    assert (RunStopped, LeaseLost, DatabaseRoleError) == pick_explicit.PASSED


def test_any_other_error_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("planted")

    monkeypatch.setattr(model, "evidence_for", boom)
    matched = type("M", (), {"playlist": []})()
    chosen = type("C", (), {"songs": []})()
    assert explicit_report(matched, chosen, {}, None) == ({}, frozenset())  # type: ignore[arg-type]
