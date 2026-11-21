"""The explicit report of a Spotify song pick (#1340, ADR 0040 section 6), as a job on the fakes.
Report only: the list is the same with or without a flag, apart from the ``explicit`` key of the
flagged songs and the provenance record ``explicit``. Never the network (netguard)."""

import json
from typing import Any

import pytest

from classbeat.spotdown import explicit_model as model
from songtools import SongEnv, song_id
from spotdownfake import FakeSpotdown, spotdown
from test_spotdown_song_analysis_job import (
    SONGS,
    docs,
    labelled,
    listed,
    run_on,
    starts,
    vid,
    waits,
)

__all__ = ["spotdown", "waits"]  # the fixtures

pytestmark = pytest.mark.usefixtures("songenv")

KEY = model.KEY


async def baseline(songenv: SongEnv) -> Any:
    """The list of an unflagged pick of the same catalogue (the report changes nothing else)."""
    return (await listed(songenv, await songenv.rider()))[1]


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
    assert all("explicit" not in s for s in revision.content["songs"])
    entries = revision.provenance[KEY]["songs"]  # spotdown's group: "unknown" for every song
    assert [e["spotdown"]["value"] for e in entries] == ["unknown"] * 8
    assert all("spotify" not in e for e in entries)


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
    # every song has spotdown's (unknown) group; Spotify's flag is recorded for the flagged two
    assert {e["position"]: e.get("spotify") for e in record["songs"]} == {
        **dict.fromkeys(range(8)), 2: True, 3: False,
    }  # fmt: skip


async def test_spotdowns_yes_tags_a_song_spotify_does_not_flag(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    docs(spotdown, explicit="yes")
    _, revision = await run_on(songenv)
    assert tagged(revision) == list(range(8))
    assert all(e["spotdown"]["value"] == "yes" for e in revision.provenance[KEY]["songs"])


_MALFORMED: list[Any] = [
    {"value": [], "confidence": 0.9, "method": "metadata", "evidence": []},
    {
        "value": "yes",
        "confidence": 0.9,
        "method": "metadata",
        "evidence": [{"source": [], "value": True}],
    },
    {"value": {"a": 1}, "confidence": [], "method": "metadata", "evidence": {}},
    [],
    {},
]


@pytest.mark.parametrize("raw", _MALFORMED)
async def test_a_malformed_spotdown_group_keeps_spotdowns_timing(
    songenv: SongEnv, spotdown: FakeSpotdown, raw: Any
) -> None:
    """A malformed ``content.explicit`` is no evidence and never costs the list its timing."""
    docs(spotdown, raw_explicit=raw)
    _, revision = await run_on(songenv)
    assert labelled(revision) == list(range(8))  # spotdown's sections kept
    assert tagged(revision) == []


async def test_a_spotdown_no_does_not_hide_spotifys_yes(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    docs(spotdown, explicit="no")
    flag(songenv, SONGS[1], True)
    _, revision = await run_on(songenv)
    assert tagged(revision) == [1]


async def test_the_flag_is_sent_to_spotdown_only_when_known(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    flag(songenv, SONGS[0], True)
    flag(songenv, SONGS[1], False)
    await run_on(songenv)
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
