"""SR C8 (ADR 0036 section 9): the Spotify sync state per revision, for the working copy (the pool
playlist), on the fakes (Spotify, the SwedeBeat gateway), against Postgres as ``classbeat_app``;
never the network.

- a studio pick's list gets a ``working_copy`` row: ``synced`` when the pool holds the list,
  read back; a failed write is ``failed`` with its category, **the pick still waits for its
  approval**, and "Sync again" (route and ``spotify_sync`` job) writes it, once per failure;
- **the race**: an older write still in flight holds the playlist's lock, so a newer revision's
  write starts only after it ended, and the playlist ends with the newer content; an older write
  that has not written yet when a newer revision is intended hands over without writing
  (``superseded``), also when it runs after the newer one;
- a "Sync again" job that ended without settling its row (cancelled, out of attempts) leaves it
  ``pending`` with no job to write it: it is shown ``failed``/``interrupted`` and can be pressed
  again; a pick's row is its job's own (one revision id per job, not per attempt);
- another user's class is not found, and the studio off answers like an unknown path.
"""

import asyncio
import json
import re
import threading
import uuid
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.swedebeat_studio import config as studio_config
from classbeat.swedebeat_studio.sync_kind import SPOTIFY_SYNC
from pickfake import POOL_14
from songtools import song_id
from spotifycore import API_HOST, Answer
from studiotools import LIST_8, StudioEnv, StudioRider

ITEMS = r"/v1/playlists/[0-9A-Za-z]{22}/items"
ONE = r"/v1/playlists/[0-9A-Za-z]{22}"
ALL_POOL = [ref for ref, _ in POOL_14]
LIST_A = [song_id(i) for i in (1, 2, 3)]
LIST_B = [song_id(i) for i in (4, 5, 6, 7)]


def uris(ids: list[str]) -> list[str | None]:
    return [f"spotify:track:{i}" for i in ids]


async def picked(studio: StudioEnv) -> tuple[StudioRider, UUID, UUID]:
    """A rider whose first pick waits for its approval: the rider, the class and the list."""
    studio.found(*ALL_POOL)
    rider = await studio.rider()
    job_id, revision_id, _ = await studio.waiting(rider)
    return rider, studio.songs.row(job_id).class_id, revision_id


def syncs(studio: StudioEnv, owner: UUID) -> list[Any]:
    return studio.songs.sql(
        "SELECT * FROM spotify_syncs WHERE owner_id = :o ORDER BY seq", o=owner
    ).all()


def plant(studio: StudioEnv, owner: UUID, class_id: UUID, ids: list[str]) -> UUID:
    """A failed working-copy row of another revision of the class, for "Sync again"."""
    revision_id = uuid.uuid4()
    studio.songs.sql(
        "INSERT INTO spotify_syncs (owner_id, class_id, revision_id, target, lock_key, "
        "track_ids, state, detail) VALUES (:o, :c, :r, 'working_copy', 'working_copy', "
        "CAST(:t AS jsonb), 'failed', 'forbidden')",
        o=owner,
        c=class_id,
        r=revision_id,
        t=json.dumps(ids),
    )
    return revision_id


def state(studio: StudioEnv, owner: UUID, revision_id: UUID) -> tuple[str, str | None]:
    row = studio.songs.sql(
        "SELECT state, detail FROM spotify_syncs WHERE owner_id = :o AND revision_id = :r",
        o=owner,
        r=revision_id,
    ).one()
    return row.state, row.detail


async def again(
    rider: StudioRider, class_id: UUID, revision_id: UUID, target: str = "working_copy"
) -> httpx.Response:
    return await rider.browser.post(f"/api/classes/{class_id}/spotify-sync/{revision_id}/{target}")


async def again_job(rider: StudioRider, class_id: UUID, revision_id: UUID) -> UUID:
    answer = await again(rider, class_id, revision_id)
    assert answer.status_code == 201, answer.text
    assert answer.json()["kind"] == "spotify_sync"
    return UUID(answer.json()["job_id"])


async def run_sync(studio: StudioEnv, job_id: UUID) -> Any:
    songs = studio.songs
    songs.sql(  # only this one is claimable now
        "UPDATE jobs SET available_at = now() + interval '1 hour' WHERE kind = 'spotify_sync' "
        "AND state = 'queued' AND id <> :id",
        id=job_id,
    )
    # A sync never takes a mix-analysis slot (``analysis_slot_by_default`` would give it one).
    songs.sql(
        "UPDATE jobs SET available_at = now(), analysis_slot = false WHERE id = :id", id=job_id
    )
    lease = songs.spot.env.settings.job_lease_seconds
    row = await queue.claim(songs.factory, "sync-w", lease, [SPOTIFY_SYNC.name])
    assert row is not None, "nothing to claim"
    assert row.job_id == job_id
    registry = KindRegistry()
    registry.register(SPOTIFY_SYNC)
    await execute(
        Execution(row),
        songs.factory,
        registry,
        RunConfig(lease_seconds=lease, spotify_calls_per_job=songs.calls_per_job),
        asyncio.Event(),
    )
    return songs.row(job_id)


def pool_entries(studio: StudioEnv, owner: UUID) -> list[str | None]:
    pool = studio.songs.pool(owner)
    return list(studio.songs.fake.playlists[pool.playlist_id].entries)


def puts(studio: StudioEnv) -> list[Any]:
    return [s for s in studio.songs.fake.requests(API_HOST, ITEMS) if s.method == "PUT"]


# --- the state of a pick's list ------------------------------------------------------------------


async def test_a_picks_list_is_synced_to_the_working_copy_and_read_back(studio: StudioEnv) -> None:
    rider, class_id, revision_id = await picked(studio)
    (row,) = syncs(studio, rider.user_id)
    assert (row.class_id, row.revision_id, row.target) == (class_id, revision_id, "working_copy")
    assert (row.state, row.detail, row.lock_key) == ("synced", None, "working_copy")
    chosen = [song_id(i) for _, i, _ in LIST_8]
    assert row.track_ids == chosen
    assert pool_entries(studio, rider.user_id) == uris(chosen)
    shown = await rider.browser.get(f"/api/classes/{class_id}/spotify-sync/{revision_id}")
    assert shown.status_code == 200, shown.text
    assert shown.json() == {"working_copy": {"state": "synced", "detail": None}, "final": None}
    for value in [*chosen, studio.songs.pool(rider.user_id).playlist_id]:
        assert value not in shown.text
    # Nothing to do for a synced list: a press starts nothing.
    answer = await again(rider, class_id, revision_id)
    assert (answer.status_code, answer.json()["detail"]) == (409, {"reason": "not_failed"})


async def test_a_failed_sync_is_visible_never_fails_the_pick_and_sync_again_writes_it(
    studio: StudioEnv,
) -> None:
    studio.found(*ALL_POOL)
    rider = await studio.rider()
    # The read after the create (#1155) and the owner check of the replace are refused: nothing
    # is written to the pool's items.
    studio.songs.fake.inject("status", method="GET", path=ONE, status=403, times=2)
    job_id, revision_id, _ = await studio.waiting(rider)  # the list still waits for approval
    class_id = studio.songs.row(job_id).class_id
    assert state(studio, rider.user_id, revision_id) == ("failed", "forbidden")
    assert puts(studio) == []
    shown = await rider.browser.get(f"/api/classes/{class_id}/spotify-sync/{revision_id}")
    assert shown.json()["working_copy"] == {"state": "failed", "detail": "forbidden"}

    sync_job = await again_job(rider, class_id, revision_id)
    assert studio.songs.row(sync_job).class_id is None  # beside the class's waiting job
    assert state(studio, rider.user_id, revision_id) == ("pending", None)
    repeated = await again(rider, class_id, revision_id)
    assert (repeated.status_code, repeated.json()["detail"]) == (409, {"reason": "not_failed"})
    done = await run_sync(studio, sync_job)
    assert (done.state, done.error_category) == ("completed", None)
    assert state(studio, rider.user_id, revision_id) == ("synced", None)
    chosen = [song_id(i) for _, i, _ in LIST_8]
    assert pool_entries(studio, rider.user_id) == uris(chosen)
    assert len(puts(studio)) == 1
    # The waiting list is untouched: it can still be approved.
    assert studio.songs.row(job_id).state == "waiting_for_approval"


async def test_a_sync_that_fails_again_is_failed_again_and_the_job_completes(
    studio: StudioEnv,
) -> None:
    rider, class_id, _ = await picked(studio)
    revision_id = plant(studio, rider.user_id, class_id, LIST_A)
    sync_job = await again_job(rider, class_id, revision_id)
    studio.songs.fake.inject("partial", method="PUT", path=ITEMS, keep=1, times=1)
    done = await run_sync(studio, sync_job)
    assert (done.state, done.error_category) == ("completed", None)
    assert state(studio, rider.user_id, revision_id) == ("failed", "playlist_mismatch")
    retried = await run_sync(studio, await again_job(rider, class_id, revision_id))
    assert retried.state == "completed"
    assert state(studio, rider.user_id, revision_id) == ("synced", None)
    assert pool_entries(studio, rider.user_id) == uris(LIST_A)


# --- a sync that ended without settling its row -------------------------------------------------


async def shown_working(rider: StudioRider, class_id: UUID, revision_id: UUID) -> Any:
    answer = await rider.browser.get(f"/api/classes/{class_id}/spotify-sync/{revision_id}")
    assert answer.status_code == 200, answer.text
    return answer.json()["working_copy"]


async def test_a_cancelled_sync_again_is_interrupted_and_can_be_pressed_again(
    studio: StudioEnv,
) -> None:
    rider, class_id, _ = await picked(studio)
    revision_id = plant(studio, rider.user_id, class_id, LIST_A)
    sync_job = await again_job(rider, class_id, revision_id)
    # While its job is queued the row is being written: shown pending, a press starts nothing.
    assert await shown_working(rider, class_id, revision_id) == {"state": "pending", "detail": None}
    busy = await again(rider, class_id, revision_id)
    assert (busy.status_code, busy.json()["detail"]) == (409, {"reason": "not_failed"})

    cancelled = await rider.browser.post(f"/api/jobs/{sync_job}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert studio.songs.row(sync_job).state == "cancelled"
    # The row stayed pending with no job left to write it: shown failed, and Sync again takes it.
    assert state(studio, rider.user_id, revision_id) == ("pending", None)
    assert await shown_working(rider, class_id, revision_id) == {
        "state": "failed",
        "detail": "interrupted",
    }
    done = await run_sync(studio, await again_job(rider, class_id, revision_id))
    assert (done.state, done.error_category) == ("completed", None)
    assert state(studio, rider.user_id, revision_id) == ("synced", None)
    assert pool_entries(studio, rider.user_id) == uris(LIST_A)


async def test_a_sync_again_that_ran_out_of_attempts_is_interrupted_and_can_be_pressed_again(
    studio: StudioEnv, monkeypatch: Any
) -> None:
    from classbeat.swedebeat_studio import sync_kind

    rider, class_id, _ = await picked(studio)
    revision_id = plant(studio, rider.user_id, class_id, LIST_A)
    sync_job = await again_job(rider, class_id, revision_id)
    studio.songs.sql("UPDATE jobs SET max_attempts = 1 WHERE id = :id", id=sync_job)

    async def crash(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the worker crashed mid-write")

    original = sync_kind.write_pool
    monkeypatch.setattr(sync_kind, "write_pool", crash)
    done = await run_sync(studio, sync_job)
    assert done.state == "failed"  # its last attempt ended by the crash: nothing settled the row
    assert state(studio, rider.user_id, revision_id) == ("pending", None)
    assert await shown_working(rider, class_id, revision_id) == {
        "state": "failed",
        "detail": "interrupted",
    }
    monkeypatch.setattr(sync_kind, "write_pool", original)
    retried = await run_sync(studio, await again_job(rider, class_id, revision_id))
    assert (retried.state, retried.error_category) == ("completed", None)
    assert state(studio, rider.user_id, revision_id) == ("synced", None)
    assert pool_entries(studio, rider.user_id) == uris(LIST_A)


async def test_a_picks_working_copy_row_is_the_jobs_own(studio: StudioEnv) -> None:
    """One revision id per pick job (not per attempt): a retried pick re-intends the same row."""
    studio.found(*ALL_POOL)
    rider = await studio.rider()
    job_id, revision_id, _ = await studio.waiting(rider)
    assert revision_id == uuid.uuid5(job_id, "studio-songs:working-copy")
    assert [r.revision_id for r in syncs(studio, rider.user_id)] == [revision_id]


# --- the race ------------------------------------------------------------------------------------


def gate_first_put(studio: StudioEnv) -> tuple[threading.Event, threading.Event, Any]:
    """Hold Spotify's answer to the next ``PUT .../items`` until the test opens the gate; the
    write is applied once the gate opens, as a slow Spotify would."""
    fake = studio.songs.fake
    entered, gate = threading.Event(), threading.Event()
    original = fake.handle

    def gated(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Answer:
        if method == "PUT" and not entered.is_set() and re.fullmatch(ITEMS, urlsplit(url).path):
            entered.set()
            assert gate.wait(20), "the test never opened the gate"
        return original(method, url, headers, body)

    fake.handle = gated  # type: ignore[method-assign]
    return entered, gate, original


async def test_an_older_write_in_flight_never_leaves_the_old_content(studio: StudioEnv) -> None:
    rider, class_id, _ = await picked(studio)
    older = plant(studio, rider.user_id, class_id, LIST_A)
    newer = plant(studio, rider.user_id, class_id, LIST_B)
    fake = studio.songs.fake
    entered, gate, original = gate_first_put(studio)

    older_job = await again_job(rider, class_id, older)
    older_run = asyncio.ensure_future(run_sync(studio, older_job))
    assert await asyncio.to_thread(entered.wait, 20), "the older PUT never arrived"
    # The older write is in flight (its PUT sent, not answered). The newer revision is intended
    # now; its job runs, and waits for the playlist's lock without sending anything.
    newer_job = await again_job(rider, class_id, newer)
    newer_run = asyncio.ensure_future(run_sync(studio, newer_job))
    seen = len(fake.requests(API_HOST))
    await asyncio.sleep(1.5)
    assert len(fake.requests(API_HOST)) == seen, "a second write started while one was in flight"
    assert not newer_run.done()

    gate.set()
    older_row, newer_row = await asyncio.gather(older_run, newer_run)
    fake.handle = original  # type: ignore[method-assign]
    assert (older_row.state, newer_row.state) == ("completed", "completed")
    # The older write was the intended one when it decided: it finished, read back. Then the
    # newer one wrote: the playlist holds the newer content.
    assert state(studio, rider.user_id, older) == ("synced", None)
    assert state(studio, rider.user_id, newer) == ("synced", None)
    assert pool_entries(studio, rider.user_id) == uris(LIST_B)
    writes = puts(studio)
    assert len(writes) == 3  # the pick's, the older's, the newer's
    older_reads = [
        s.number
        for s in fake.requests(API_HOST, ITEMS)
        if s.method == "GET" and writes[1].number < s.number < writes[2].number
    ]
    assert older_reads, "the older write's read-back ran before the newer write started"


async def test_a_stale_write_hands_over_without_writing(studio: StudioEnv) -> None:
    rider, class_id, _ = await picked(studio)
    older = plant(studio, rider.user_id, class_id, LIST_A)
    newer = plant(studio, rider.user_id, class_id, LIST_B)
    older_job = await again_job(rider, class_id, older)
    newer_job = await again_job(rider, class_id, newer)  # intended after the older one
    before = len(studio.songs.fake.requests(API_HOST))
    done = await run_sync(studio, older_job)
    assert (done.state, done.error_category) == ("completed", None)
    assert state(studio, rider.user_id, older) == ("failed", "superseded")
    after = studio.songs.fake.requests(API_HOST)[before:]
    assert [s for s in after if s.method in ("PUT", "POST")] == []  # nothing written
    assert (await run_sync(studio, newer_job)).state == "completed"
    assert state(studio, rider.user_id, newer) == ("synced", None)
    assert pool_entries(studio, rider.user_id) == uris(LIST_B)


async def test_an_older_write_that_runs_after_the_newer_one_writes_nothing(
    studio: StudioEnv,
) -> None:
    rider, class_id, _ = await picked(studio)
    older = plant(studio, rider.user_id, class_id, LIST_A)
    newer = plant(studio, rider.user_id, class_id, LIST_B)
    older_job = await again_job(rider, class_id, older)
    newer_job = await again_job(rider, class_id, newer)
    assert (await run_sync(studio, newer_job)).state == "completed"
    count = len(puts(studio))
    assert (await run_sync(studio, older_job)).state == "completed"
    assert len(puts(studio)) == count
    assert state(studio, rider.user_id, older) == ("failed", "superseded")
    assert pool_entries(studio, rider.user_id) == uris(LIST_B)
    # A superseded list can be made the working copy again, by the user only.
    assert (await run_sync(studio, await again_job(rider, class_id, older))).state == "completed"
    assert pool_entries(studio, rider.user_id) == uris(LIST_A)


# --- isolation -----------------------------------------------------------------------------------


async def test_another_users_class_is_not_found_and_the_studio_off_too(studio: StudioEnv) -> None:
    rider, class_id, revision_id = await picked(studio)
    other = await studio.rider()
    path = f"/api/classes/{class_id}/spotify-sync/{revision_id}"
    shown = await other.browser.get(path)
    assert (shown.status_code, shown.json()["detail"]) == (404, {"reason": "not_found"})
    answer = await again(other, class_id, revision_id)
    assert (answer.status_code, answer.json()["detail"]) == (404, {"reason": "not_found"})
    unknown = await again(rider, class_id, revision_id, "elsewhere")
    assert unknown.status_code == 404
    studio_config.configure(studio_config.StudioConfig(frozenset()))
    off = await rider.browser.get(path)
    assert (off.status_code, off.json()["detail"]) == (404, {"reason": "not_found"})
