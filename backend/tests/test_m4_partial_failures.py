"""M4 done-when, partial failure at each request through a full job (ADR 0008 section 2; issue
SEC1, #185).

The operation-level matrix lives in ``test_playlists_writes.py`` (a test kind that calls the
writes). Here the same failures are injected into the real ``design_with_songs`` job, at each
request of each of its four writes: the pool's create and replace (the pick) and the final
playlist's create and fill (after Approve). Each case runs the job until it stops being retried and
checks how it ended, how many playlists exist (never a second one for the same purpose), what the
user's playlists hold and whether the class got its music. Kinds of failure: ``refuse`` (never
sent), ``drop`` (applied, the answer lost), ``noid`` (applied, answered without an id), a 5xx
answer (not applied, uncertain to classbeat), ``partial`` (only the first 3 tracks applied) and
a read-back that shows another list. Fakes only: no network, no real account.
"""

from typing import Any

import pytest

from songtools import CHOSEN, POOL, SongEnv

pytestmark = pytest.mark.usefixtures("songenv")

MINE = "/v1/me/playlists"
ITEMS = r"/v1/playlists/[0-9A-Za-z]{22}/items"
CREATE = {"method": "POST", "path": MINE}
PUT = {"method": "PUT", "path": ITEMS}
READ = {"method": "GET", "path": ITEMS}
MAX_RUNS = 4


def uris(ids: list[str]) -> list[str | None]:
    return [f"spotify:track:{i}" for i in ids]


async def until_settled(songenv: SongEnv, job_id: Any) -> tuple[Any, int]:
    """Run the job's attempts until it is no longer ``queued``: (its row, the runs made)."""
    for runs in range(1, MAX_RUNS + 1):
        row = await songenv.run(job_id)
        if row.state != "queued":
            return row, runs
    raise AssertionError("still queued after every attempt")


# (injection, its options, how the pick ends, its category, runs, playlists in Spotify)
POOL_CASES = [
    # The create never left: failed for certain, the retry takes the row over.
    ("refuse", CREATE, "waiting_for_approval", None, 2, 1),
    # Applied, the answer lost or without an id: found by its ref, the pick goes on.
    ("drop", CREATE, "waiting_for_approval", None, 1, 1),
    ("noid", CREATE, "waiting_for_approval", None, 1, 1),
    # A 5xx: nothing to find, so uncertain; never retried, nothing chosen.
    ("status", {**CREATE, "status": 503}, "failed", "uncertain_write", 1, 0),
    # The replace: refused before the write, retried; dropped after it, read back equal.
    ("refuse", PUT, "waiting_for_approval", None, 2, 1),
    ("drop", PUT, "waiting_for_approval", None, 1, 1),
    # A partial write answered 500: uncertain, the read shows another list, the retry replaces.
    ("partial", {**PUT, "keep": 3, "status": 500}, "waiting_for_approval", None, 2, 1),
    # A partial write answered 200: the read-back is definite and different.
    ("partial", {**PUT, "keep": 3}, "failed", "playlist_mismatch", 1, 1),
    # The read-back fails: uncertain, the reconciliation reads it equal.
    ("status", {**READ, "status": 500}, "waiting_for_approval", None, 1, 1),
    ("duplicate", READ, "failed", "playlist_mismatch", 1, 1),
]


@pytest.mark.parametrize(("kind", "options", "state", "category", "runs", "made"), POOL_CASES)
async def test_a_failure_at_each_request_of_the_pool_write(
    songenv: SongEnv,
    kind: str,
    options: dict[str, Any],
    state: str,
    category: str | None,
    runs: int,
    made: int,
) -> None:
    rider = await songenv.rider()
    songenv.fake.inject(kind, **options)
    job_id = await songenv.created(rider)
    row, ran = await until_settled(songenv, job_id)
    assert (row.state, row.error_category, ran) == (state, category, runs)
    assert len(songenv.fake.playlists) == made  # never a second pool
    pool = songenv.pool(rider.user_id)
    if state == "waiting_for_approval":
        assert songenv.fake.playlists[pool.playlist_id].entries == uris(POOL)
    if category == "uncertain_write":
        assert pool.playlist_id is None
        [create] = songenv.operations(rider.user_id, "spotify_create_playlist")
        assert create.state == "uncertain"
        # A pool row is never the user's to resolve: no Check again, no Do it anyway.
        for action in ("check-again", "do-it-anyway"):
            answer = await rider.browser.post(f"/api/external-operations/{create.id}/{action}")
            assert answer.status_code == 404
    # The class never got a list it was not shown.
    assert songenv.sql(
        "SELECT count(*) FROM class_revisions WHERE owner_id = :o AND kind = 'song_list'",
        o=rider.user_id,
    ).scalar_one() == (1 if state == "waiting_for_approval" else 0)


FINAL_CASES = [
    ("refuse", CREATE, "completed", None, 2, 2),
    ("drop", CREATE, "completed", None, 1, 2),
    ("noid", CREATE, "completed", None, 1, 2),
    # A 5xx on the final create: the user's to decide (Check again / Do it anyway).
    ("status", {**CREATE, "status": 503}, "failed", "uncertain_write", 1, 1),
    ("refuse", PUT, "completed", None, 2, 2),
    ("drop", PUT, "completed", None, 1, 2),
    ("partial", {**PUT, "keep": 3, "status": 500}, "completed", None, 2, 2),
    ("partial", {**PUT, "keep": 3}, "failed", "playlist_mismatch", 1, 2),
    ("status", {**READ, "status": 500}, "completed", None, 1, 2),
    ("duplicate", READ, "failed", "playlist_mismatch", 1, 2),
]


@pytest.mark.parametrize(("kind", "options", "state", "category", "runs", "made"), FINAL_CASES)
async def test_a_failure_at_each_request_of_the_final_playlist(
    songenv: SongEnv,
    kind: str,
    options: dict[str, Any],
    state: str,
    category: str | None,
    runs: int,
    made: int,
) -> None:
    rider = await songenv.rider()
    job_id, revision_id = await songenv.approved(rider)
    songenv.fake.inject(kind, **options)
    row, ran = await until_settled(songenv, job_id)
    assert (row.state, row.error_category, ran) == (state, category, runs)
    assert len(songenv.fake.playlists) == made  # the pool and at most one final playlist
    finals = songenv.class_playlists(rider.user_id)
    current = songenv.sql(
        "SELECT current_revision_id FROM classes WHERE id = :c", c=row.class_id
    ).scalar_one()
    if state == "completed":
        [final] = finals
        assert (final.state, final.revision_id) == ("verified", revision_id)
        assert songenv.fake.playlists[final.playlist_id].entries == uris(CHOSEN)
        assert songenv.revision(current).kind == "draft"
    else:
        assert current is None or songenv.revision(current).kind != "draft"  # nothing designed
        assert all(f.state != "verified" for f in finals)
    if category == "uncertain_write":
        assert finals == []
        [create] = [
            o for o in songenv.operations(rider.user_id, "spotify_create_playlist") if o.class_id
        ]
        assert create.state == "uncertain"
        listed = (await rider.browser.get("/api/external-operations")).json()
        assert str(create.id) in str(listed)  # offered to the user
    if category == "playlist_mismatch":
        [final] = finals
        assert final.state == "created"
        assert (
            songenv.fake.playlists[final.playlist_id].entries != uris(CHOSEN) or kind != "partial"
        )
