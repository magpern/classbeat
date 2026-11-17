"""A playlist Spotify made public on create (#1155, roadmap Amendment 27, ADR 0008 section 2): the
pool's and a final playlist's. classbeat asks for private once more (``PUT /playlists/{id}``) only
for the id its own create returned, then writes either way; a collaborative or another user's
playlist is still refused with nothing written, and an existing playlist's privacy is never
changed. Fakes only (``spotifycore``): no network, no real account.
"""

from typing import Any

import pytest

from classbeat.playlists import common
from jobtools import new_class
from playlisttools import MANY, TRACKS, PlayEnv

ONE = r"/v1/playlists/[0-9A-Za-z]{22}"


def details(penv: PlayEnv) -> list[str]:
    """The playlist ids of every change-details request (``PUT /playlists/{id}``)."""
    return [s.path.rsplit("/", 1)[-1] for s in penv.api(ONE, "PUT")]


def uris(track_ids: Any) -> list[str | None]:
    return [f"spotify:track:{t}" for t in track_ids]


async def final_playlist(penv: PlayEnv, owner: Any) -> tuple[Any, Any]:
    """A class's final playlist made by a ``final_create`` job; (class id, made)."""
    class_id = new_class(penv.admin, owner)
    revision_id, content_hash = penv.song_list(owner, class_id)
    job_id = penv.job(
        owner, class_id, action="final_create", revision_id=str(revision_id),
        content_hash=content_hash.hex(),
    )  # fmt: skip
    assert (await penv.run(job_id)).state == "completed"
    return class_id, penv.results[-1]


async def fill(penv: PlayEnv, owner: Any, class_id: Any, made: Any) -> Any:
    job_id = penv.job(owner, class_id, action="final_fill", class_playlist_id=str(made.id))
    return await penv.run(job_id)


# --- the pool ----------------------------------------------------------------------------------


async def test_a_pool_created_public_is_made_private_then_written_in_order(penv: PlayEnv) -> None:
    owner = await penv.user()
    penv.fake.create_public = True
    row = await penv.run(penv.job(owner, tracks=MANY))
    assert (row.state, row.error_category) == ("completed", None)
    [written] = penv.results
    playlist = penv.fake.playlists[written.playlist_id]
    assert (playlist.public, playlist.collaborative) == (False, False)
    assert penv.entries(written.playlist_id) == uris(MANY)
    assert details(penv) == [written.playlist_id]  # only the id the create returned
    # the create, its read-back, the change, its read-back, then the replace as before
    assert [(s.method, s.path.rsplit("/", 1)[-1]) for s in penv.api()][:6] == [
        ("POST", "playlists"), ("GET", written.playlist_id), ("PUT", written.playlist_id),
        ("GET", written.playlist_id), ("GET", written.playlist_id), ("PUT", "items"),
    ]  # fmt: skip


async def test_a_pool_that_stays_public_is_still_written_in_order(penv: PlayEnv) -> None:
    owner = await penv.user()
    penv.fake.create_public = True
    penv.fake.ignore_privacy = True  # Spotify answers 200 and keeps it public
    row = await penv.run(penv.job(owner, tracks=MANY))
    assert (row.state, row.error_category) == ("completed", None)
    [written] = penv.results
    assert penv.fake.playlists[written.playlist_id].public is True
    assert penv.entries(written.playlist_id) == uris(MANY)
    assert details(penv) == [written.playlist_id]  # asked once, not again


async def test_a_refused_change_never_fails_the_pick(penv: PlayEnv) -> None:
    owner = await penv.user()
    penv.fake.create_public = True
    penv.fake.inject("status", method="PUT", path=ONE, status=403)
    row = await penv.run(penv.job(owner, tracks=TRACKS))
    assert (row.state, row.error_category) == ("completed", None)
    [written] = penv.results
    assert penv.entries(written.playlist_id) == uris(TRACKS)


async def test_a_retried_attempt_after_the_create_still_asks_for_private(penv: PlayEnv) -> None:
    """The worker dies at the read after the create; the next attempt finds the pool row naming
    this job's own created playlist and asks for private before the write."""
    owner = await penv.user()
    penv.fake.create_public = True
    job_id = penv.job(owner, tracks=TRACKS)
    await penv.crash_during(job_id, "GET", ONE)
    pool = penv.pool(owner)
    assert pool.playlist_id is not None
    assert details(penv) == []
    row = await penv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    assert details(penv) == [pool.playlist_id]
    assert penv.fake.playlists[pool.playlist_id].public is False
    assert penv.entries(pool.playlist_id) == uris(TRACKS)


async def test_an_existing_public_pool_is_written_and_its_privacy_never_changed(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    first = penv.job(owner)
    assert (await penv.run(first)).state == "completed"
    playlist_id = penv.pool(owner).playlist_id
    penv.fake.playlists[playlist_id].public = True  # the user made it public
    penv.fake.seen.clear()
    row = await penv.run(penv.job(owner, tracks=MANY))
    assert (row.state, row.error_category) == ("completed", None)
    assert details(penv) == []  # not the create's own id: never changed
    assert penv.fake.playlists[playlist_id].public is True
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


@pytest.mark.parametrize("ignored", [False, True])
async def test_a_final_playlist_created_public_is_filled_in_order(
    penv: PlayEnv, ignored: bool
) -> None:
    owner = await penv.user()
    penv.fake.create_public = True
    penv.fake.ignore_privacy = ignored
    class_id, made = await final_playlist(penv, owner)
    assert details(penv) == [made.playlist_id]
    assert penv.fake.playlists[made.playlist_id].public is ignored
    row = await fill(penv, owner, class_id, made)
    assert (row.state, row.error_category) == ("completed", None)
    assert penv.entries(made.playlist_id) == uris(TRACKS)
    [playlist_row] = penv.class_playlists(owner)
    assert (playlist_row.state, bytes(playlist_row.content_hash)) == (
        "verified",
        common.ids_hash(TRACKS),
    )
    assert details(penv) == [made.playlist_id]  # the fill never changes privacy


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


async def test_a_new_collaborative_playlist_is_made_private_and_not_collaborative(
    penv: PlayEnv,
) -> None:
    """The change sends ``collaborative: false`` too, for the create's own id only."""
    owner = await penv.user()
    original = penv.fake.handle

    def collaborative_create(m: str, url: str, headers: dict[str, str], body: bytes | None) -> Any:
        answer = original(m, url, headers, body)
        if m == "POST" and url.endswith("/v1/me/playlists"):
            for playlist in penv.fake.playlists.values():
                playlist.collaborative = True
        return answer

    penv.fake.handle = collaborative_create  # type: ignore[method-assign]
    class_id, made = await final_playlist(penv, owner)
    penv.fake.handle = original  # type: ignore[method-assign]
    playlist = penv.fake.playlists[made.playlist_id]
    assert (playlist.public, playlist.collaborative) == (False, False)
    assert details(penv) == [made.playlist_id]
    assert (await fill(penv, owner, class_id, made)).state == "completed"
