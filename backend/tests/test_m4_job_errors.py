"""M4 done-when, every error category through a full job (ADR 0009 section 8, ADR 0008 sections 1
and 2; issue SEC1, #185).

Each case injects one Spotify answer into a real ``design_with_songs`` job (the pick's pool create,
the token refresh a pick needs, or the second phase's fill), runs the job with the real kind and
reads the job back through ``GET /api/jobs/{id}``: its state, its category and the plain sentence
the page shows (``error_message``), never a provider's text. The connection's status flips only
for ``invalid_grant`` and ``app_misconfigured`` (section 6). The retryable ones are retried, with a
rate limit's ``Retry-After`` honoured, and the retry finishes the pick.

The categories that exist only at connect (``not_allowlisted``, ``scope_mismatch``,
``invalid_response``, ``access_denied``, and ``account_changed`` at the callback) are reached
through a full connect in ``test_spotify_errors.py`` (the callback's ``result`` and the status
sentence); ``account_changed`` through a job is in ``test_design_with_songs_approve.py`` and
``test_design_with_songs_resume.py``. Fakes only: no network, no real account.
"""

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from classbeat.jobs.sentences import SONG_SENTENCES
from classbeat.songs import song_list
from classbeat.spotify import sentence
from ctx import make_context
from songtools import Rider, SongEnv
from spotifyfake import CLIENT_A, USER_A

pytestmark = pytest.mark.usefixtures("songenv")

MINE = "/v1/me/playlists"
ONE = r"/v1/playlists/[0-9A-Za-z]{22}"
TOKEN = "/api/token"
SECRETS = ("Injected failure", "API rate limit exceeded", "not yours")


async def shown(rider: Rider, job_id: Any) -> dict[str, Any]:
    response = await rider.browser.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def spotify_status(songenv: SongEnv, rider: Rider) -> tuple[str, str | None]:
    row = await songenv.spot.connection(make_context(rider.user_id))
    return row.status, row.last_error


async def due_for_refresh(songenv: SongEnv, rider: Rider) -> None:
    """The stored access token is about to expire: the pick's first Spotify call refreshes."""
    await songenv.spot.seed(make_context(rider.user_id), CLIENT_A, USER_A, expires_in=10)


# (injection, its options, whether the pick refreshes first, category, connection status after)
FAILED = [
    ("status", {"method": "POST", "path": MINE, "status": 403, "message": "not yours"}, False,
     "forbidden", "connected"),
    ("rate_limited", {"method": "POST", "path": MINE, "retry_after": 3 * 3600}, False,
     "quota_exhausted", "connected"),
    ("invalid_grant", {"path": TOKEN}, True, "invalid_grant", "needs_reconnect"),
    ("invalid_client", {"path": TOKEN}, True, "app_misconfigured", "needs_reconnect"),
]  # fmt: skip


@pytest.mark.parametrize(("kind", "options", "refresh", "category", "status"), FAILED)
async def test_a_category_that_ends_the_pick_shows_its_sentence(
    songenv: SongEnv,
    kind: str,
    options: dict[str, Any],
    refresh: bool,
    category: str,
    status: str,
) -> None:
    rider = await songenv.rider()
    if refresh:
        await due_for_refresh(songenv, rider)
    songenv.fake.inject(kind, **options)
    job_id = await songenv.created(rider)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", category)
    view = await shown(rider, job_id)
    assert (view["state"], view["error_category"]) == ("failed", category)
    assert view["error_message"] == sentence(category)
    assert not any(text in str(view) for text in SECRETS)  # never Spotify's own words
    assert (await spotify_status(songenv, rider))[0] == status
    assert songenv.fake.playlists == {}  # nothing was created
    assert await songenv.claim() is None  # not retried


async def test_not_found_on_the_final_playlist_ends_the_second_phase(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, _ = await songenv.approved(rider)
    songenv.fake.inject("status", method="GET", path=ONE, status=404)  # the fill's owner check
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "not_found")
    view = await shown(rider, job_id)
    assert view["error_message"] == sentence("not_found")
    assert (await spotify_status(songenv, rider))[0] == "connected"
    [final] = songenv.class_playlists(rider.user_id)
    assert songenv.fake.playlists[final.playlist_id].entries == []  # created, never written


@pytest.mark.parametrize(
    ("kind", "options", "at_least"),
    [
        # rate_limited: Retry-After honoured by the backoff
        ("rate_limited", {"method": "POST", "path": MINE, "retry_after": 600}, 600),
        # upstream_unavailable: the create never left
        ("refuse", {"method": "POST", "path": MINE}, 0),
    ],
)
async def test_a_retryable_category_is_retried_and_the_retry_finishes_the_pick(
    songenv: SongEnv, kind: str, options: dict[str, Any], at_least: int
) -> None:
    rider = await songenv.rider()
    songenv.fake.inject(kind, **options)
    job_id = await songenv.created(rider)
    started = datetime.now(UTC)
    row = await songenv.run(job_id)
    assert (row.state, row.attempt) == ("queued", 1), (row.state, row.error_category)
    assert row.available_at >= started + timedelta(seconds=at_least)
    view = await shown(rider, job_id)
    assert view["state"] == "queued"
    assert not any(text in str(view) for text in SECRETS)
    assert (await spotify_status(songenv, rider))[0] == "connected"
    assert (await songenv.run(job_id)).state == "waiting_for_approval"
    assert len(songenv.fake.playlists) == 1  # one pool, whatever the retry did


async def test_rate_limits_never_park_a_worker_thread_and_end_the_job_when_attempts_run_out(
    songenv: SongEnv,
) -> None:
    """The library never sleeps on a 429 (``spotify_max_retry_wait=0``): an attempt that meets
    one ends at once and the job's backoff waits instead. Three in a row use up the attempts."""
    rider = await songenv.rider()
    songenv.fake.inject("rate_limited", method="POST", path=MINE, retry_after=30, times=3)
    job_id = await songenv.created(rider)
    states = []
    for _ in range(3):
        began = time.monotonic()
        row = await songenv.run(job_id)
        states.append(row.state)
        assert time.monotonic() - began < 20
    assert states == ["queued", "queued", "failed"]
    assert songenv.fake.playlists == {}
    assert await songenv.claim() is None


@pytest.mark.xfail(
    strict=True,
    reason="ADR 0009 section 8: 3 rate_limited retries in a row on one job are quota_exhausted; "
    "the job ends rate_limited today (reported in #185 for the Architect)",
)
async def test_three_rate_limits_in_a_row_end_quota_exhausted(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    songenv.fake.inject("rate_limited", method="POST", path=MINE, retry_after=30, times=3)
    job_id = await songenv.created(rider)
    rows = [await songenv.run(job_id) for _ in range(3)]
    assert (rows[-1].state, rows[-1].error_category) == ("failed", "quota_exhausted")


@pytest.mark.xfail(
    strict=True,
    reason="ADR 0009 section 8: quota_exhausted says 'Try again in about N hours' from the "
    "Retry-After; a job keeps no hint, so the page says 1 hour (reported in #185)",
)
async def test_the_quota_sentence_names_the_hours_spotify_asked_for(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    songenv.fake.inject("rate_limited", method="POST", path=MINE, retry_after=3 * 3600)
    job_id = await songenv.created(rider)
    await songenv.run(job_id)
    assert (await shown(rider, job_id))["error_message"] == sentence("quota_exhausted", 3 * 3600)


@pytest.mark.parametrize(
    ("spoil", "category"),
    [
        ("public", "pool_unusable"),
        ("collaborative", "pool_unusable"),
        ("revoke", "consent_missing"),
    ],
)
async def test_the_pools_own_categories_show_their_sentence(
    songenv: SongEnv, spoil: str, category: str
) -> None:
    rider = await songenv.rider()
    await songenv.waiting(rider)
    pool = songenv.pool(rider.user_id)
    job_id = await songenv.created(rider)  # admitted with the consent; spoiled before it runs
    if spoil == "revoke":
        songenv.sql("DELETE FROM spotify_pool WHERE owner_id = :o", o=rider.user_id)
    else:
        setattr(songenv.fake.playlists[pool.playlist_id], spoil, True)
    before = list(songenv.fake.playlists[pool.playlist_id].entries)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", category)
    assert (await shown(rider, job_id))["error_message"] == SONG_SENTENCES[category]
    assert songenv.fake.playlists[pool.playlist_id].entries == before  # nothing written
    assert (await spotify_status(songenv, rider))[0] == "connected"


async def test_a_stored_list_replaced_whole_under_the_wait_is_a_hash_mismatch(
    songenv: SongEnv,
) -> None:
    """The hash check of the kind (``songs.admit.approve_check``): the waiting list's row is
    replaced behind the app by another valid list with its own matching hash and fingerprint (two
    songs swapped), so the list reads back as valid.
    The job still waits on the old hash, which is what the browser sends: only re-reading the
    stored list and comparing its hash with the approved one refuses it."""
    rider = await songenv.rider()
    job_id, revision_id, digest = await songenv.waiting(rider)
    stored = songenv.revision(revision_id)
    content = dict(stored.content)
    first, second, *rest = content["songs"]
    content["songs"] = [second, first, *rest]
    swapped = song_list.content_hash(content)
    music = dict(stored.music)
    music["fingerprint"] = song_list.fingerprint(tuple(s["spotify_id"] for s in content["songs"]))
    songenv.sql(
        "UPDATE class_revisions SET content = CAST(:c AS jsonb), content_hash = :h, "
        "music = CAST(:m AS jsonb) WHERE id = :id",
        c=json.dumps(content),
        h=swapped,
        m=json.dumps(music),
        id=revision_id,
    )
    before = songenv.counts()
    answer = await rider.approve(job_id, revision_id, digest)
    assert (answer.status_code, answer.json()) == (409, {"detail": {"reason": "hash_mismatch"}})
    assert songenv.row(job_id).approved_at is None
    assert await songenv.claim() is None
    assert songenv.counts() == before
