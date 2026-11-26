"""A composer class may start without Spotify and asks about its music (ADR 0038 amendment of
2026-11-24, decisions 5 and 6, #1495), through the jobs API, the hand-off and the worker side,
against Postgres, the fake MOWL and ``FakeProvider``: the reading stores the music decision, the
sketch needs no Spotify, the song search refuses ``music_undecided`` and writes nothing, the answer
is stored and read back; without the composer at hand-off the older route refuses ``needs_spotify``
as Amendment 25 says. Never the network, a real account or a model."""

from typing import Any
from uuid import UUID

import httpx
import pytest

from classbeat import connections
from classbeat.design import interpret_admit, music_kind
from classbeat.songs import config as songs_config
from ctx import make_context
from interprettools import READING, REQUEST
from sketchtools import SketchEnv

pytestmark = pytest.mark.usefixtures("jenv", "picking")

PLAYLISTS = ["0123456789abcdefABCDEF", "ABCDEF0123456789abcdef"]
EIGHTIES = f"{REQUEST} to 80s hits"
TASTE: dict[str, Any] = {
    **READING,
    "music_taste": {
        "eras": [{"year_from": 1980, "year_to": 1989, "origin": "explicit", "quote": "80s"}],
        "hits": {"value": True, "origin": "explicit", "quote": "hits"},
    },
}
HITS_80S: dict[str, Any] = {
    "eras": [{"year_from": 1980, "year_to": 1989}],
    "genres": [],
    "hits": True,
    "language": None,
}


@pytest.fixture
def picking(monkeypatch: pytest.MonkeyPatch) -> None:
    """Song picking admits the user, so a class with no music is ``needs_spotify`` (a connection
    can be made), not ``song_picking_unavailable``."""

    async def admits(*_args: Any) -> bool:
        return True

    monkeypatch.setattr(interpret_admit, "song_picking", admits)


async def put_music(skenv: SketchEnv, class_id: UUID, body: Any) -> httpx.Response:
    browser = skenv.renv.browser
    response = await browser.client.put(
        f"/api/classes/{class_id}/music", headers=await browser.headers(), json=body
    )
    browser.client.cookies.clear()
    return response


async def detail(skenv: SketchEnv, class_id: UUID) -> dict[str, Any]:
    got = await skenv.renv.browser.get(f"/api/classes/{class_id}")
    assert got.status_code == 200, got.text
    return got.json()


def song_jobs(skenv: SketchEnv, class_id: UUID) -> int:
    return skenv.sql(
        "SELECT count(*) FROM jobs WHERE class_id = :c AND kind = 'design_with_songs'", c=class_id
    ).scalar_one()


async def read_without_spotify(skenv: SketchEnv, reading: dict[str, Any] | None = None) -> UUID:
    class_id, job_id = await skenv.ienv.interpreted(reading, spotify=False)
    given = skenv.ienv.stored_input(job_id)
    assert (given.get("spotify_connection_id"), given["composer"]) == (None, True)
    return class_id


async def test_with_no_spotify_a_request_with_the_music_left_open_reaches_a_sketch(
    skenv: SketchEnv,
) -> None:
    """AC 1 and 2: admitted, read, sketched and edited with no Spotify connection; the song
    search refuses ``music_undecided`` and writes nothing."""
    class_id = await read_without_spotify(skenv)
    stored = skenv.ienv.interpretation(class_id)
    assert stored["music_decision"] == {
        "state": "undecided",
        "choice": None,
        "sources": [],
        "note": None,
    }
    assert stored["music"] == {"source": "pick"}  # read as with Spotify: the search asks for it
    sketch = await skenv.designed(class_id)
    [job] = skenv.ienv.design_jobs(class_id)
    assert (job.kind, skenv.ienv.stored_input(job.id)["mode"]) == ("design_draft", "sketch")
    assert job.credit_state == "reserved"  # the sketch takes the class credit, as today
    shown = await detail(skenv, class_id)
    assert (shown["composer"], shown["music_decision"]) == (
        True,
        {"state": "undecided", "choice": None, "playlists": 0},
    )
    job_id, _ = await skenv.renv.revise(class_id, sketch, "soundless_revise_good", "Seven bursts")
    row = skenv.renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    edited = skenv.renv.current(class_id)
    assert edited not in (None, sketch)
    before = skenv.ienv.counts()
    for retry in (False, True):
        refused = await skenv.renv.browser.post(
            f"/api/classes/{class_id}/fill-songs", {"retry": True} if retry else None
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"] == {"reason": "music_undecided"}
    assert skenv.ienv.counts() == before  # no job, no credit
    assert song_jobs(skenv, class_id) == 0
    assert "fill" not in skenv.ienv.interpretation(class_id)  # nothing stored on the class


async def test_the_answer_is_stored_and_shown_and_later_leaves_it_open(skenv: SketchEnv) -> None:
    class_id = await read_without_spotify(skenv)
    await skenv.designed(class_id)
    body = {
        "choice": "playlists",
        "playlists": [{"kind": "spotify_playlist", "id": p} for p in PLAYLISTS],
    }
    before = skenv.ienv.counts()
    answered = await put_music(skenv, class_id, body)
    assert answered.status_code == 200, answered.text
    assert skenv.ienv.counts() == before  # no job and no credit
    assert answered.json() == {"state": "decided", "choice": "playlists", "playlists": 2}
    assert PLAYLISTS[0] not in answered.text  # a count, never an id
    stored = skenv.ienv.interpretation(class_id)["music_decision"]
    assert [s["id"] for s in stored["sources"]] == PLAYLISTS
    assert (await detail(skenv, class_id))["music_decision"] == answered.json()
    stamp = "SELECT updated_at FROM classes WHERE id = :c"
    at = skenv.sql(stamp, c=class_id).scalar_one()
    again = await put_music(skenv, class_id, body)  # idempotent: the same state, nothing written
    assert (again.status_code, again.json()) == (200, answered.json())
    assert skenv.sql(stamp, c=class_id).scalar_one() == at
    later = await put_music(skenv, class_id, {"choice": "later"})
    assert later.json() == {"state": "undecided", "choice": None, "playlists": 0}
    refused = await skenv.renv.browser.post(f"/api/classes/{class_id}/fill-songs", None)
    assert refused.json()["detail"]["reason"] == "music_undecided"


@pytest.mark.parametrize(
    ("reading", "more"),
    [
        ({**READING, "music_taste": {"hits": {"value": True, "origin": "explicit"}}}, {}),
        (READING, {"music_override": {"source": "none"}}),
    ],
)
async def test_a_request_that_says_what_the_music_is_is_decided_as_requested(
    skenv: SketchEnv, reading: dict[str, Any], more: dict[str, Any]
) -> None:
    class_id, _ = await skenv.ienv.interpreted(reading, spotify=False, **more)
    decision = skenv.ienv.interpretation(class_id)["music_decision"]
    assert (decision["state"], decision["choice"]) == ("decided", "request")


async def test_with_no_spotify_a_taste_is_kept_sketched_and_used_by_the_fill_once_connected(
    skenv: SketchEnv,
) -> None:
    """The Architect's decision on #1507: a disconnected instructor whose request describes the
    music gets a sketch; the taste stays in the brief (the sketch ignores it) and the fill takes
    it once Spotify is connected and the working playlist allowed."""
    class_id, _ = await skenv.ienv.interpreted(TASTE, spotify=False, request=EIGHTIES)
    stored = skenv.ienv.interpretation(class_id)
    assert (stored["music_decision"]["state"], stored["music_decision"]["choice"]) == (
        "decided",
        "request",
    )
    assert stored["music"] == {"source": "pick"}
    assert stored["brief"]["music_taste"] == HITS_80S
    await skenv.designed(class_id)  # the sketch, with no Spotify
    shown = await detail(skenv, class_id)
    assert shown["music_decision"] == {"state": "decided", "choice": "request", "playlists": 0}
    path = f"/api/classes/{class_id}/fill-songs"
    refused = await skenv.renv.browser.post(path, None)
    assert (refused.status_code, refused.json()["detail"]) == (409, {"reason": "not_connected"})
    assert song_jobs(skenv, class_id) == 0
    await skenv.ienv.connect_spotify()
    ctx = make_context(skenv.ienv.owner)
    assert await connections.give_pool_consent(skenv.renv.denv.ops.factory, ctx, 1)
    skenv.sql(
        "INSERT INTO entitlements (owner_id, features, source) "
        "VALUES (:o, ARRAY['song_picking'], 'grant')",
        o=skenv.ienv.owner,
    )
    songs_config.configure(songs_config.SongConfig(enabled=True))
    try:
        made = await skenv.renv.browser.post(path, None)
    finally:
        songs_config.configure(None)
    assert made.status_code == 201, made.text
    job = skenv.ienv.stored_input(UUID(made.json()["job_id"]))
    assert (job["mode"], job["brief"]["music_taste"]) == ("fill", HITS_80S)


async def test_with_no_spotify_a_named_playlist_is_kept_as_the_music_and_sketched(
    skenv: SketchEnv,
) -> None:
    """The same decision: a named Spotify playlist needs Spotify only when it is used."""
    named = {**READING, "music": {"value": 0, "origin": "explicit"}}
    class_id, _ = await skenv.ienv.interpreted(named, spotify=False)
    stored = skenv.ienv.interpretation(class_id)
    assert stored["music"]["source"] == "mowl_spotify_playlist"
    assert stored["music_decision"]["choice"] == "request"
    await skenv.designed(class_id)
    [job] = skenv.ienv.design_jobs(class_id)
    assert skenv.ienv.stored_input(job.id)["mode"] == "sketch"


async def test_a_class_read_with_the_composer_falls_back_to_needs_spotify_without_it(
    skenv: SketchEnv,
) -> None:
    """The Architect's addendum: admitted with the composer and no Spotify, handed off once the
    plan lost its coverage: the older route needs Spotify and stops ``needs_spotify``, stored,
    with no job and no credit."""
    class_id = await read_without_spotify(skenv)
    music_kind.configure(music_kind.SoundlessGate(enabled=True, fake_covers=False))
    refused = await skenv.ienv.handoff(class_id)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "needs_spotify")
    assert skenv.ienv.interpretation(class_id)["refusal"] == {
        "reason": "needs_spotify",
        "counts": {},
    }
    assert skenv.ienv.design_jobs(class_id) == []
    assert skenv.ienv.credits() == 0


async def test_without_the_composer_a_request_with_no_spotify_is_refused_at_admit(
    skenv: SketchEnv,
) -> None:
    """AC 6: the older flow keeps Amendment 25's early refusal (no class, no job, no credit)."""
    music_kind.configure(music_kind.SoundlessGate(enabled=True, fake_covers=False))
    before = skenv.ienv.counts()
    refused = await skenv.ienv.start(spotify=False)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "needs_spotify")
    assert skenv.ienv.counts() == before
