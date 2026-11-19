"""A working (pool) or final playlist is created public from the start (#1287, roadmap Amendment
30, ADR 0008 section 2): one ``POST /me/playlists`` with ``public: true`` and ``collaborative:
false``, and no change-details request (``PUT /playlists/{id}``) or read-back for its privacy.
A collaborative or another user's playlist is still refused with nothing written, an existing
playlist's privacy is never changed, and a ``public`` that is not a boolean is refused. A token
without ``playlist-modify-public`` cannot create (or write) a public playlist: ``scope_mismatch``.
Fakes only (``spotifycore``): no network, no real account.
"""

import json
from typing import Any

import pytest

from classbeat.jobs.sentences import failure_sentence
from classbeat.playlists import common
from jobtools import new_class
from playlisttools import MANY, TRACKS, PlayEnv
from spotifycore import PRIVATE_SCOPES

ONE = r"/v1/playlists/[0-9A-Za-z]{22}"
CREATE = "/v1/me/playlists"
SCOPE_SENTENCE = "Spotify did not give the access we need. Connect again and allow it."


def details(penv: PlayEnv) -> list[str]:
    """The playlist ids of every change-details request (``PUT /playlists/{id}``)."""
    return [s.path.rsplit("/", 1)[-1] for s in penv.api(ONE, "PUT")]


def uris(track_ids: Any) -> list[str | None]:
    return [f"spotify:track:{t}" for t in track_ids]


def record_create_bodies(penv: PlayEnv) -> list[dict[str, Any]]:
    """Every create's JSON body, as the fake received it (the fake keeps no bodies itself)."""
    bodies: list[dict[str, Any]] = []
    original = penv.fake.handle

    def recording(m: str, url: str, headers: dict[str, str], body: bytes | None) -> Any:
        if m == "POST" and url.split("?", 1)[0].endswith(CREATE) and body is not None:
            bodies.append(json.loads(body))
        return original(m, url, headers, body)

    penv.fake.handle = recording  # type: ignore[method-assign]
    return bodies


def final_create_job(penv: PlayEnv, owner: Any) -> tuple[Any, Any]:
    """A ``final_create`` job for a new class's approved song list; (class id, job id)."""
    class_id = new_class(penv.admin, owner)
    revision_id, content_hash = penv.song_list(owner, class_id)
    job_id = penv.job(
        owner, class_id, action="final_create", revision_id=str(revision_id),
        content_hash=content_hash.hex(),
    )  # fmt: skip
    return class_id, job_id


async def final_playlist(penv: PlayEnv, owner: Any) -> tuple[Any, Any]:
    """A class's final playlist made by a ``final_create`` job; (class id, made)."""
    class_id, job_id = final_create_job(penv, owner)
    assert (await penv.run(job_id)).state == "completed"
    return class_id, penv.results[-1]


async def fill(penv: PlayEnv, owner: Any, class_id: Any, made: Any) -> Any:
    job_id = penv.job(owner, class_id, action="final_fill", class_playlist_id=str(made.id))
    return await penv.run(job_id)


def only_private_scopes(penv: PlayEnv) -> None:
    """Every token the fake issued so far grants only the two private scopes (a connection made
    before Amendment 27)."""
    for grant in (*penv.fake.access.values(), *penv.fake.refresh.values()):
        grant.scope = PRIVATE_SCOPES


# --- the pool ----------------------------------------------------------------------------------


async def test_a_new_pool_is_created_public_in_one_call_then_written(penv: PlayEnv) -> None:
    owner = await penv.user()
    bodies = record_create_bodies(penv)
    row = await penv.run(penv.job(owner, tracks=MANY))
    assert (row.state, row.error_category) == ("completed", None)
    [written] = penv.results
    [body] = bodies
    assert (body["public"], body["collaborative"]) == (True, False)
    assert body["name"] == common.POOL_NAME
    playlist = penv.fake.playlists[written.playlist_id]
    assert (playlist.public, playlist.collaborative) == (True, False)
    assert penv.entries(written.playlist_id) == uris(MANY)
    assert details(penv) == []  # never asked for private
    # the create, then the replace's owner check and its write: no read-back for privacy
    assert [(s.method, s.path.rsplit("/", 1)[-1]) for s in penv.api()][:3] == [
        ("POST", "playlists"), ("GET", written.playlist_id), ("PUT", "items"),
    ]  # fmt: skip


async def test_a_retried_attempt_after_the_create_writes_without_a_privacy_step(
    penv: PlayEnv,
) -> None:
    """The worker dies while Spotify handles the create; the next attempt reconciles it (the
    playlist naming the row's ``ref``), finds it public and writes it, without any change of its
    privacy and without a second create."""
    owner = await penv.user()
    job_id = penv.job(owner, tracks=TRACKS)
    await penv.crash_during(job_id, "POST", CREATE)
    row = await penv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    pool = penv.pool(owner)
    assert pool.playlist_id is not None
    assert details(penv) == []
    assert penv.fake.playlists[pool.playlist_id].public is True
    assert penv.entries(pool.playlist_id) == uris(TRACKS)
    assert len(penv.api(CREATE, "POST")) == 1  # created once


@pytest.mark.parametrize("public", [False, True])
async def test_an_existing_pool_is_written_and_its_privacy_never_changed(
    penv: PlayEnv, public: bool
) -> None:
    owner = await penv.user()
    first = penv.job(owner)
    assert (await penv.run(first)).state == "completed"
    playlist_id = penv.pool(owner).playlist_id
    penv.fake.playlists[playlist_id].public = public  # the user changed it in Spotify
    penv.fake.seen.clear()
    row = await penv.run(penv.job(owner, tracks=MANY))
    assert (row.state, row.error_category) == ("completed", None)
    assert details(penv) == []
    assert not penv.api(CREATE, "POST")
    assert penv.fake.playlists[playlist_id].public is public
    assert penv.entries(playlist_id) == uris(MANY)


@pytest.mark.parametrize("value", [None, "yes"])
async def test_a_pool_whose_public_is_not_a_boolean_is_refused(penv: PlayEnv, value: Any) -> None:
    owner = await penv.user()
    first = penv.job(owner)
    assert (await penv.run(first)).state == "completed"
    playlist_id = penv.pool(owner).playlist_id
    penv.fake.playlists[playlist_id].public = value
    penv.fake.seen.clear()
    row = await penv.run(penv.job(owner, tracks=MANY))
    assert (row.state, row.error_category) == ("failed", "pool_unusable")
    assert not penv.writes(playlist_id)


# --- a final playlist --------------------------------------------------------------------------


async def test_a_final_playlist_is_created_public_in_one_call_and_filled_in_order(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    bodies = record_create_bodies(penv)
    class_id, made = await final_playlist(penv, owner)
    [body] = bodies
    assert (body["public"], body["collaborative"]) == (True, False)
    assert penv.fake.playlists[made.playlist_id].public is True
    assert [s.method for s in penv.api()] == ["POST"]  # the create alone: no read-back
    row = await fill(penv, owner, class_id, made)
    assert (row.state, row.error_category) == ("completed", None)
    assert penv.entries(made.playlist_id) == uris(TRACKS)
    [playlist_row] = penv.class_playlists(owner)
    assert (playlist_row.state, bytes(playlist_row.content_hash)) == (
        "verified",
        common.ids_hash(TRACKS),
    )
    assert details(penv) == []  # the fill never changes privacy


async def test_a_final_playlist_made_collaborative_is_refused_with_nothing_written(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    class_id, made = await final_playlist(penv, owner)
    penv.fake.playlists[made.playlist_id].collaborative = True
    penv.fake.seen.clear()
    row = await fill(penv, owner, class_id, made)
    assert (row.state, row.error_category) == ("failed", "forbidden")
    assert not penv.writes()  # no item write, no change of its privacy
    assert penv.entries(made.playlist_id) == []


# --- a connection without ``playlist-modify-public`` -------------------------------------------


@pytest.mark.parametrize("purpose", ["pool", "final"])
async def test_a_create_without_the_public_scope_asks_to_reconnect(
    penv: PlayEnv, purpose: str
) -> None:
    owner = await penv.user()
    only_private_scopes(penv)
    if purpose == "pool":
        job_id = penv.job(owner, tracks=TRACKS)
    else:
        _, job_id = final_create_job(penv, owner)
    row = await penv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "scope_mismatch")
    assert not penv.fake.playlists  # nothing created
    assert [s.status for s in penv.api(CREATE, "POST")] == [403]  # asked once, refused
    [create] = penv.operations(owner, "spotify_create_playlist")
    assert (create.state, create.error_category) == ("failed", "scope_mismatch")
    assert failure_sentence("scope_mismatch") == SCOPE_SENTENCE


@pytest.mark.parametrize("purpose", ["pool", "final"])
async def test_a_public_playlist_without_the_public_scope_is_not_written(
    penv: PlayEnv, purpose: str
) -> None:
    """Created with the public scope, then the grant lost it: the write asks to reconnect."""
    owner = await penv.user()
    if purpose == "pool":
        assert (await penv.run(penv.job(owner))).state == "completed"
        playlist_id = penv.pool(owner).playlist_id
        before = penv.entries(playlist_id)
        only_private_scopes(penv)
        row = await penv.run(penv.job(owner, tracks=MANY))
    else:
        class_id, made = await final_playlist(penv, owner)
        playlist_id = made.playlist_id
        before = penv.entries(playlist_id)
        only_private_scopes(penv)
        row = await fill(penv, owner, class_id, made)
    assert (row.state, row.error_category) == ("failed", "scope_mismatch")
    assert penv.fake.playlists[playlist_id].public is True
    assert penv.entries(playlist_id) == before  # nothing written, nothing deleted


async def test_an_existing_private_pool_still_works_with_only_the_private_scopes(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    assert (await penv.run(penv.job(owner))).state == "completed"
    playlist_id = penv.pool(owner).playlist_id
    penv.fake.playlists[playlist_id].public = False  # a pool made before Amendment 30
    only_private_scopes(penv)
    row = await penv.run(penv.job(owner, tracks=MANY))
    assert (row.state, row.error_category) == ("completed", None)
    assert penv.entries(playlist_id) == uris(MANY)
    assert details(penv) == []
