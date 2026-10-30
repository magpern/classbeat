"""``design_with_songs``: ``admit`` and the pool (ADR 0008 sections 2 and 4; issue SJ1).

Creation refusals write nothing (no job, no class). One pick per user at a time, none counted
while it waits; two users pick at once with their own pools; MOWL must show the pool just written
(``pool_not_synced``, retried once); tracks without a BPM are left out (``pool_too_small`` when
too few remain); a pool write that stays uncertain ends the pick without asking the user, and the
next pick supersedes it. Fakes only.
"""

import asyncio
from typing import Any
from uuid import UUID

import pytest

from classbeat import songs
from classbeat.jobs.runner import RunConfig, execute
from optools import TOKEN_B
from songtools import (
    CHOSEN,
    KIND,
    POOL,
    SongEnv,
    choose_turns,
    song_id,
)
from spotifyfake import USER_B

pytestmark = pytest.mark.usefixtures("songenv")

ITEMS = r"/v1/playlists/[0-9A-Za-z]{22}/items"
REPLACE = "spotify_replace_items"


def refused(response: Any, reason: str, status: int = 409) -> None:
    assert (response.status_code, response.json()["detail"]["reason"]) == (status, reason)


def jobs_of(songenv: SongEnv, owner: UUID) -> int:
    return songenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner).scalar_one()


def classes_of(songenv: SongEnv, owner: UUID) -> int:
    return songenv.sql("SELECT count(*) FROM classes WHERE owner_id = :o", o=owner).scalar_one()


# --- admit --------------------------------------------------------------------------------------


async def test_song_picking_off_consent_missing_and_resume_create_nothing(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    songs.configure(songs.SongConfig(enabled=False))
    refused(await rider.create(), "song_picking_off")
    songs.configure(songs.SongConfig(enabled=True, sync_wait_seconds=0.3))
    resume = {"mode": "resume", "resume": {"song_list_revision_id": str(rider.user_id)}}
    assert (await rider.create(**resume)).status_code == 422  # a resume names its class
    no_consent = await songenv.rider(spotify_user=USER_B, mowl_token=TOKEN_B, consent=False)
    refused(await no_consent.create(), "consent_missing")
    for owner in (rider.user_id, no_consent.user_id):
        assert (jobs_of(songenv, owner), classes_of(songenv, owner)) == (0, 0)
    assert songenv.counts() == (0, 0, 0)


async def test_the_brief_limits_and_the_connections_are_checked(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    long_brief = {"length_min": 30, "text": "€" * 2000}  # 6000 bytes: over classes.brief
    assert (await rider.create(brief=long_brief)).status_code == 422
    refused(
        await rider.create(spotify_connection_id=str(rider.mowl_connection_id)), "not_connected"
    )
    assert jobs_of(songenv, rider.user_id) == 0


async def test_a_second_pick_is_pool_busy_until_the_first_waits(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    first = await songenv.created(rider)
    refused(await rider.create(), "pool_busy")
    execution = await songenv.claim()  # running now
    assert execution is not None
    refused(await rider.create(), "pool_busy")
    await execute(
        execution,
        songenv.factory,
        songenv.registry(),
        RunConfig(lease_seconds=songenv.spot.env.settings.job_lease_seconds),
        asyncio.Event(),
    )
    assert songenv.row(first).state == "waiting_for_approval"
    second = await rider.create()  # a waiting pick no longer needs the pool
    assert second.status_code == 201, second.text
    assert jobs_of(songenv, rider.user_id) == 2


async def test_another_users_pick_is_not_pool_busy(songenv: SongEnv) -> None:
    alice = await songenv.rider()
    bob = await songenv.rider(spotify_user=USER_B, mowl_token=TOKEN_B)
    await songenv.created(alice)
    assert (await bob.create()).status_code == 201


async def test_a_seed_must_be_the_classs_newest_unapproved_list_of_an_ended_job(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    job_id, revision, _ = await songenv.waiting(rider)
    class_id = str(songenv.row(job_id).class_id)
    seed = {"song_list_revision_id": str(revision), "replace": [2]}
    refused(await rider.create(class_id=class_id, seed=seed), "class_busy")  # still waiting
    cancelled = await rider.browser.post(f"/api/jobs/{job_id}/cancel")
    assert cancelled.json()["state"] == "cancelled"
    out_of_list = {"song_list_revision_id": str(revision), "replace": [8]}  # 8 songs: 0 to 7
    refused(await rider.create(class_id=class_id, seed=out_of_list), "invalid_input")
    unknown = {"song_list_revision_id": str(rider.user_id), "replace": [2]}
    refused(await rider.create(class_id=class_id, seed=unknown), "invalid_input")
    assert (
        await rider.create(class_id=class_id, seed={**seed, "replace": [2, 2]})
    ).status_code == 422
    assert (await rider.create(seed=seed)).status_code == 422  # a seed needs its class
    accepted = await rider.create(class_id=class_id, seed=seed)
    assert accepted.status_code == 201, accepted.text


async def test_a_replace_keeps_the_other_songs_in_their_slots(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, revision, _ = await songenv.waiting(rider)
    class_id = songenv.row(job_id).class_id
    await rider.browser.post(f"/api/jobs/{job_id}/cancel")
    # Slot 2 (a climb) is replaced by a climb from a new pool of six candidates.
    songenv.script = {
        "model": "fake-model",
        "turns": [
            *_replace_pool_turns(),
            *choose_turns(
                [("kept", 0, "warmup"), ("kept", 1, "build"), ("pool", 0, "climb")]
                + [("kept", i, role) for i, role in _KEPT_REST]
            ),
        ],
    }
    seed = {"song_list_revision_id": str(revision), "replace": [2]}
    new_job, new_revision, _ = await songenv.waiting(rider, class_id=str(class_id), seed=seed)
    content = songenv.revision(new_revision).content
    ids = [s["spotify_id"] for s in content["songs"]]
    assert content["mode"] == "replace"
    assert ids[:2] == CHOSEN[:2]
    assert ids[3:] == CHOSEN[3:]
    assert ids[2] == song_id(13)
    assert content["songs"][2]["new"] is True
    assert [s["new"] for i, s in enumerate(content["songs"]) if i != 2] == [False] * 7
    assert songenv.revision(new_revision).parent_id == revision
    assert new_job != job_id


# Kept indexes 2 to 6 of the seed's other slots (3 to 7 of LIST_8) with their roles.
_KEPT_REST = [(2, "recovery"), (3, "sprint"), (4, "sprint"), (5, "recovery"), (6, "cooldown")]


def _replace_pool_turns() -> list[dict[str, Any]]:
    from pickfake import call, entries, turn

    # ``beta`` finds tracks 10 to 19 (refs 0 to 9); 10 to 12 are kept songs, so refs 3 to 8.
    pool = entries(
        (3, "climb"), (4, "climb"), (5, "climb"), (6, "build"), (7, "build"), (8, "climb")
    )
    return [
        turn(call("search_tracks", {"query": "beta"}, expect={"ok": True})),
        turn(call("submit_pool", pool, expect={"accepted": True})),
    ]


# --- the pool -----------------------------------------------------------------------------------


async def test_two_users_pick_at_once_with_their_own_pools(songenv: SongEnv) -> None:
    alice = await songenv.rider()
    bob = await songenv.rider(spotify_user=USER_B, mowl_token=TOKEN_B)
    jobs = [await songenv.created(alice), await songenv.created(bob)]
    executions = [await songenv.claim(), await songenv.claim()]
    assert all(e is not None for e in executions)
    config = RunConfig(lease_seconds=songenv.spot.env.settings.job_lease_seconds)
    await asyncio.gather(
        *(
            execute(e, songenv.factory, songenv.registry(), config, asyncio.Event())
            for e in executions
            if e is not None
        )
    )
    assert [songenv.row(j).state for j in jobs] == ["waiting_for_approval"] * 2
    pools = [songenv.pool(alice.user_id), songenv.pool(bob.user_id)]
    assert pools[0].playlist_id != pools[1].playlist_id
    for pool, user in zip(pools, ("rider-one", USER_B), strict=True):
        playlist = songenv.fake.playlists[pool.playlist_id]
        assert playlist.owner == user
        assert playlist.public is False
        assert playlist.entries == [f"spotify:track:{i}" for i in POOL]
    # Every write to a pool carried the token of the pool's own user.
    for pool, user in zip(pools, ("rider-one", USER_B), strict=True):
        writes = [s for s in songenv.api() if pool.playlist_id in s.path]
        assert writes
        assert all(s.token_user == user for s in writes)


async def test_a_mowl_that_still_shows_the_old_pool_is_pool_not_synced_retried_once(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    job_id, _, _ = await songenv.waiting(rider)
    await rider.browser.post(f"/api/jobs/{job_id}/cancel")
    pool_id = songenv.pool(rider.user_id).playlist_id
    songenv.mowl.stale[pool_id] = POOL[:5]  # MOWL keeps an older track list
    second = await songenv.created(rider)
    row = await songenv.run(second)
    assert (row.state, row.attempt) == ("queued", 1)
    assert songenv.sql(
        "SELECT payload ->> 'category' FROM job_events WHERE job_id = :id "
        "AND kind = 'retry_scheduled'", id=second,
    ).scalar_one() == "pool_not_synced"  # fmt: skip
    row = await songenv.run(second)
    assert (row.state, row.error_category) == ("failed", "pool_not_synced")
    # Both misses stopped before choosing: no choose stage, no choose run, no revision.
    assert "choose" not in songenv.stages(second)
    assert (
        songenv.sql(
            "SELECT count(*) FROM agent_runs WHERE job_id = :id AND stage = 'choose_songs'",
            id=second,
        ).scalar_one()
        == 0
    )
    assert (
        songenv.sql(
            "SELECT count(*) FROM class_revisions WHERE job_id = :id", id=second
        ).scalar_one()
        == 0
    )


async def test_a_mowl_that_catches_up_on_the_retry_gives_a_list(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, _, _ = await songenv.waiting(rider)
    await rider.browser.post(f"/api/jobs/{job_id}/cancel")
    pool_id = songenv.pool(rider.user_id).playlist_id
    songenv.mowl.stale[pool_id] = POOL[:5]
    second = await songenv.created(rider)
    assert (await songenv.run(second)).state == "queued"
    del songenv.mowl.stale[pool_id]
    assert (await songenv.run(second)).state == "waiting_for_approval"


async def test_tracks_without_a_bpm_are_left_out_and_counted(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    songenv.mowl.no_bpm = {song_id(13)}  # a candidate the list does not use
    _job_id, revision, _ = await songenv.waiting(rider)
    content = songenv.revision(revision).content
    assert content["omitted_no_bpm"] == 1
    assert [s["spotify_id"] for s in content["songs"]] == CHOSEN


async def test_too_few_usable_tracks_is_pool_too_small(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    songenv.mowl.no_bpm = set(POOL[5:])  # 5 usable, 8 songs to find
    job_id = await songenv.created(rider)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "pool_too_small")
    assert "choose" not in songenv.stages(job_id)


async def test_an_uncertain_pool_write_ends_the_pick_and_the_next_pick_supersedes_it(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    first_job, _, _ = await songenv.waiting(rider)
    await rider.browser.post(f"/api/jobs/{first_job}/cancel")
    pool_id = songenv.pool(rider.user_id).playlist_id
    songenv.fake.inject("drop", method="PUT", path=ITEMS)
    songenv.fake.inject("status", method="GET", path=ITEMS, status=500, times=2)  # unreadable
    second = await songenv.created(rider)
    row = await songenv.run(second)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    stuck = songenv.operations(rider.user_id, REPLACE)[-1]
    assert (stuck.state, stuck.class_id) == ("uncertain", None)
    # The user is never asked: a pool row has no Check again / Do it anyway.
    for action in ("check-again", "do-it-anyway"):
        answer = await rider.browser.post(f"/api/external-operations/{stuck.id}/{action}")
        assert answer.status_code == 404
    assert (await rider.browser.get(f"/api/external-operations/{stuck.id}")).status_code == 404
    listed = await rider.browser.get(f"/api/external-operations?job_id={second}")
    assert listed.status_code == 200
    assert str(stuck.id) not in listed.text
    # The next pick supersedes it and writes under its own key.
    third = await songenv.created(rider)
    assert (await songenv.run(third)).state == "waiting_for_approval"
    old = next(o for o in songenv.operations(rider.user_id, REPLACE) if o.id == stuck.id)
    assert (old.state, old.resolved_by) == ("failed", "worker")
    newest = songenv.operations(rider.user_id, REPLACE)[-1]
    assert newest.idempotency_key.startswith(f"{third}:pool:")
    assert songenv.pool(rider.user_id).playlist_id == pool_id


async def test_the_kind_is_registered_with_both_connections() -> None:
    from classbeat.jobs import KINDS

    kind = KINDS.get(KIND)
    assert kind is not None
    assert kind is songs.DESIGN_WITH_SONGS
    assert kind.consumes_credit
    assert kind.admit is not None
    assert kind.approve_check is not None
    assert songs.DesignWithSongsInput.CONNECTIONS == ("mowl", "spotify")
