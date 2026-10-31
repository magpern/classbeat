"""``design_with_songs`` phase 1 (ADR 0008 sections 2 to 5; issue SJ1): from the brief to the
``song_list`` revision and the wait, against the fake Spotify, the fake MOWL and ``FakeProvider``.
No network, no real account."""

import hashlib
import json
import threading
from typing import Any

import pytest
from mowl import spotify_web

from classbeat.connections import ConnectionFailure
from classbeat.db import user_session
from classbeat.design.revision import canonical
from classbeat.playlists import common
from classbeat.songs import search, song_list
from ctx import make_context
from songtools import CHOSEN, POOL, SongEnv, song_id
from spotifycore import album_images

pytestmark = pytest.mark.usefixtures("songenv")


async def test_a_pick_ends_waiting_with_its_song_list(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, revision_id, digest = await songenv.waiting(rider)
    row = songenv.row(job_id)
    # The wait holds no lease (no worker slot) and has its expiry.
    assert (row.leased_by, row.lease_expires_at, row.approved_at) == (None, None, None)
    assert row.expires_at is not None
    assert songenv.stages(job_id) == ["pool_search", "pool_write", "pool_analyse", "choose"]
    assert songenv.kinds(job_id)[-2:] == ["revision_created", "waiting_for_approval"]

    stored = songenv.revision(revision_id)
    assert (stored.kind, stored.class_id, stored.job_id) == ("song_list", row.class_id, job_id)
    content: dict[str, Any] = stored.content
    # The hash is SHA-256 of the canonical content, and it is what the job waits on.
    assert bytes(stored.content_hash) == hashlib.sha256(canonical(content)).digest()
    assert bytes(stored.content_hash).hex() == digest
    assert content["v"] == 2  # v: 2 adds each song's cover (ADR 0013 section 9)
    assert content["mode"] == "pick"
    assert (content["target_sec"], content["total_sec"]) == (1800, 1857)
    assert content["crossfade_sec"] == 9.0
    assert content["omitted_no_bpm"] == 0
    assert [s["spotify_id"] for s in content["songs"]] == CHOSEN
    first = content["songs"][0]
    # Titles, artists, BPM and lengths are MOWL's analysis of the pool, never the model's.
    assert first == {
        "spotify_id": song_id(0),
        "title": "Ride Song 0 alpha",
        "artist": "Made-up Band 0",
        "role": "warmup",
        "bpm": 100.0,
        "length_sec": 240,
        "new": True,
        # the search hit's album cover, the narrowest image of at least 64 px
        "cover": album_images("FakeAlbum" + "0" * 13)[-1]["url"],
    }
    assert stored.music == {
        "source": "claude_pick",
        "fingerprint": common.ids_hash(CHOSEN).hex(),
        "spotify_user_id": "rider-one",
        # The connections' generations the pick used: phase 2 refuses after a reconnect.
        "generations": {"mowl": 1, "spotify": 1},
    }
    provenance = stored.provenance
    assert provenance["origin"] == "design_with_songs"
    assert set(provenance["stage_versions"]) == {"pick_pool", "choose_songs"}

    # The pool playlist holds exactly the candidates, in order, and MOWL wrapped it once.
    pool = songenv.pool(rider.user_id)
    assert pool.playlist_id is not None
    assert songenv.fake.playlists[pool.playlist_id].entries == [f"spotify:track:{i}" for i in POOL]
    assert [sid for _, sid in songenv.mowl.attaches] == [pool.playlist_id]
    # Nothing the model was sent names a track id, a playlist id or the Spotify user.
    sent = json.dumps([[str(t) for t in p.sent] for p in songenv.providers])
    for value in [*POOL, pool.playlist_id, "rider-one", "spotify:track"]:
        assert value not in sent


async def test_the_stored_list_reads_back_with_its_hash(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, revision_id, digest = await songenv.waiting(rider)
    class_id = songenv.row(job_id).class_id
    async with user_session(songenv.factory, make_context(rider.user_id)) as db:
        found = await song_list.load(db, rider.user_id, class_id, revision_id)
    assert found is not None
    assert found.content_hash.hex() == digest
    assert found.track_ids == tuple(CHOSEN)
    assert found.account == "rider-one"


async def test_the_spotify_search_runs_off_the_event_loop(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop_thread = threading.get_ident()
    threads: list[int] = []
    original = spotify_web.search_tracks

    def recording(creds: Any, query: str, *args: Any, **kwargs: Any) -> Any:
        threads.append(threading.get_ident())
        return original(creds, query, *args, **kwargs)

    monkeypatch.setattr(spotify_web, "search_tracks", recording)
    rider = await songenv.rider()
    await songenv.waiting(rider)
    assert len(threads) == 2
    assert loop_thread not in threads


async def test_a_search_that_loses_the_connection_records_its_category(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#198 item 15: a search failure that is no tool error ends the agent run with the job's
    category, not ``internal``."""

    async def lost(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionFailure("not_connected")

    monkeypatch.setattr(search.common, "call", lost)
    rider = await songenv.rider()
    job_id = await songenv.created(rider)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    outcomes = songenv.sql(
        "SELECT stage, outcome FROM agent_runs WHERE job_id = :id", id=job_id
    ).all()
    assert [(r.stage, r.outcome) for r in outcomes] == [("pick_pool", "not_connected")]


def test_the_hash_covers_the_exact_ids_and_order() -> None:
    def content(ids: list[str]) -> dict[str, Any]:
        return {"v": 1, "songs": [{"spotify_id": i, "bpm": 120.0} for i in ids]}

    base = song_list.content_hash(content(CHOSEN))
    assert song_list.content_hash(content(CHOSEN)) == base
    assert song_list.content_hash(content([CHOSEN[1], CHOSEN[0], *CHOSEN[2:]])) != base
    assert song_list.content_hash(content([*CHOSEN[:-1], song_id(19)])) != base
    assert song_list.content_hash(content(CHOSEN[:-1])) != base
