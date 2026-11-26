"""#1535: MOWL's Spotify wrapper is a snapshot, so every pool write is attached again.

``FakeMowlPool`` makes a new MOWL playlist on each attach holding the Spotify playlist's tracks at
that moment and never follows a later edit, as MOWL does. A second and a third pick of the same
user, each with another pool, must reach ``choose`` (before the fix, the second one reused the
first pick's wrapper and timed out ``pool_not_synced``); each pick's analysis reads the wrapper of
its own write, the user's link names the newest one, and a retried attempt of the same write
reuses its attach (no second PUT). Fakes only, never the network (``tests/netguard.py``).
"""

from typing import Any
from uuid import UUID

import pytest

from pickfake import POOL_14
from songtools import POOL, SongEnv, pick_script, song_id

pytestmark = pytest.mark.usefixtures("songenv")

ATTACH = "mowl_attach_spotify_playlist"
# The second pick's pool: the first one with another sprint the list does not use (14 for 9).
OTHER_14 = [(14, role) if ref == 9 else (ref, role) for ref, role in POOL_14]


def link(songenv: SongEnv, owner: UUID, source_id: str) -> Any:
    return songenv.sql(
        "SELECT id, playlist_id FROM mowl_playlist_links WHERE owner_id = :o "
        "AND source = 'spotify' AND source_id = :s",
        o=owner,
        s=source_id,
    ).one()


def tracks_read(songenv: SongEnv) -> list[int]:
    """The MOWL playlists whose track list the jobs read, in order (every read)."""
    return [
        int(path.split("/")[3])
        for path in songenv.mowl.reads
        if path.startswith("/v1/Playlists/") and path.endswith("/tracks")
    ]


async def pick(songenv: SongEnv, rider: Any, pool: list[tuple[int, str]]) -> UUID:
    """One pick of ``pool`` run to its wait, then cancelled so the next pick may start."""
    songenv.script = pick_script(pool)
    job_id, _, _ = await songenv.waiting(rider)
    assert (await rider.browser.post(f"/api/jobs/{job_id}/cancel")).status_code == 200
    return job_id


async def test_a_second_and_a_third_pick_with_other_pools_reach_choose(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    jobs = [
        await pick(songenv, rider, POOL_14),
        await pick(songenv, rider, OTHER_14),
        await pick(songenv, rider, POOL_14),  # back to the first list, in a new job
    ]
    for job_id in jobs:
        assert "choose" in songenv.stages(job_id)
    pool_id = songenv.pool(rider.user_id).playlist_id
    # One attach per write, each a new wrapper holding that write's tracks.
    assert [sid for _, sid in songenv.mowl.attaches] == [pool_id] * 3
    wrappers = sorted(songenv.mowl.snapshots)
    assert len(set(wrappers)) == 3
    held = [sorted(songenv.mowl.snapshots[w]) for w in wrappers]
    second = sorted(song_id(r) for r, _ in OTHER_14)
    assert held == [sorted(POOL), second, sorted(POOL)]
    # Every read went to the newest wrapper of its pick, never an older one.
    reads = tracks_read(songenv)
    assert sorted(set(reads)) == wrappers
    assert reads == sorted(reads)
    # The user's link names the newest wrapper; one attach row per write, all succeeded.
    assert link(songenv, rider.user_id, pool_id).playlist_id == wrappers[-1]
    rows = songenv.operations(rider.user_id, ATTACH)
    assert [r.state for r in rows] == ["succeeded"] * 3
    assert len({r.idempotency_key for r in rows}) == 3
    assert all(r.idempotency_key.startswith(f"spotify:{pool_id}:") for r in rows)
    assert [int(r.external_id) for r in rows] == wrappers


async def test_a_retried_attempt_reuses_the_attach_of_its_own_write(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    await pick(songenv, rider, POOL_14)
    pool_id = songenv.pool(rider.user_id).playlist_id
    songenv.script = pick_script(OTHER_14)
    songenv.mowl.stale[pool_id] = POOL[:5]  # MOWL lists other tracks for every wrapper of it
    second = await songenv.created(rider)
    row = await songenv.run(second)
    assert (row.state, row.attempt) == ("queued", 1)  # pool_not_synced, retried once
    attaches = len(songenv.mowl.attaches)
    assert attaches == 2
    del songenv.mowl.stale[pool_id]
    songenv.script = pick_script(OTHER_14)
    assert (await songenv.run(second)).state == "waiting_for_approval"
    # The retry wrote nothing new (the same list, reused by key) and attached nothing new.
    assert len(songenv.mowl.attaches) == attaches
    newest = max(songenv.mowl.snapshots)
    assert link(songenv, rider.user_id, pool_id).playlist_id == newest
    assert tracks_read(songenv)[-1] == newest


async def test_a_wrapper_that_follows_edits_still_works(songenv: SongEnv) -> None:
    """The fix does not depend on the snapshot: a MOWL that follows Spotify edits passes too."""
    songenv.mowl.snapshot = False
    rider = await songenv.rider()
    await pick(songenv, rider, POOL_14)
    second = await pick(songenv, rider, OTHER_14)
    assert "choose" in songenv.stages(second)
