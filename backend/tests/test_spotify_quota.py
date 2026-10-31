"""ADR 0009 section 8's own limits on a job's Spotify use (issue #300): the per-job request cap
``CLASSBEAT_SPOTIFY_CALLS_PER_JOB``, three Spotify rate limits in a row as ``quota_exhausted``
(a MOWL rate limit does not count), and Spotify's own sentences for its shared categories.

Each case runs the real ``design_with_songs`` job against the fake Spotify, the fake MOWL and
``FakeProvider``, and reads the job back through the jobs API. The cap counts every request to
the two Spotify hosts the job's attempts send (the token endpoint too), across stages and
attempts; the request that would go over it is never sent. Fakes only: no network, no account.
"""

import urllib.request
from typing import Any
from uuid import UUID

import pytest

from classbeat.jobs import settle
from classbeat.jobs.credits import UnmeteredLedger
from ctx import make_context
from songtools import _TRACKS, Rider, SongEnv  # pyright: ignore[reportPrivateUsage]
from spotifyfake import CLIENT_A, USER_A

pytestmark = pytest.mark.usefixtures("songenv")

MINE = "/v1/me/playlists"
ITEMS = r"/v1/playlists/[0-9A-Za-z]{22}/items"


class Ledger(UnmeteredLedger):
    def __init__(self) -> None:
        self.committed: list[UUID] = []
        self.released: list[UUID] = []

    async def commit(self, db: Any, ctx: Any, job_id: UUID) -> None:
        self.committed.append(job_id)

    async def release(self, db: Any, ctx: Any, job_id: UUID) -> None:
        self.released.append(job_id)


async def settled(songenv: SongEnv, job_id: UUID) -> tuple[str, Ledger]:
    ledger = Ledger()
    await settle(songenv.factory, ledger)
    await settle(songenv.factory, ledger)  # once only
    return songenv.row(job_id).credit_state, ledger


async def message(rider: Rider, job_id: UUID) -> str | None:
    response = await rider.browser.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()["error_message"]


def sent(songenv: SongEnv) -> int:
    return len(songenv.fake.requests())


async def pick_requests(songenv: SongEnv) -> int:
    """The Spotify requests one ordinary pick sends to its wait (a fresh user, a new pool)."""
    rider = await songenv.rider()
    before = sent(songenv)
    job_id, _, _ = await songenv.waiting(rider)
    used = sent(songenv) - before
    assert songenv.row(job_id).spotify_calls == used  # stored with the attempt's ending
    return used


async def test_a_job_may_send_exactly_its_cap(songenv: SongEnv) -> None:
    needed = await pick_requests(songenv)
    assert needed > 3
    songenv.calls_per_job = needed
    rider = await songenv.rider()
    before = sent(songenv)
    job_id, _, _ = await songenv.waiting(rider)
    assert sent(songenv) - before == needed
    assert songenv.row(job_id).spotify_calls == needed


async def test_the_request_over_the_cap_is_never_sent_and_the_job_ends_limit_exceeded(
    songenv: SongEnv,
) -> None:
    needed = await pick_requests(songenv)
    songenv.calls_per_job = needed - 1
    rider = await songenv.rider()
    before = sent(songenv)
    job_id = await songenv.created(rider)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "limit_exceeded")
    assert sent(songenv) - before == needed - 1  # the one over the cap never left
    assert row.spotify_calls == needed - 1
    assert await songenv.claim() is None  # never retried
    state, ledger = await settled(songenv, job_id)
    assert (state, ledger.committed, ledger.released) == ("released", [], [job_id])


async def test_the_cap_counts_across_attempts(songenv: SongEnv) -> None:
    needed = await pick_requests(songenv)
    songenv.calls_per_job = needed
    rider = await songenv.rider()
    # The first attempt's pool create never leaves: retried, and what it sent stays counted.
    songenv.fake.inject("refuse", method="POST", path=MINE)
    job_id = await songenv.created(rider)
    first = await songenv.run(job_id)
    assert first.state == "queued"
    assert first.spotify_calls > 0
    before = sent(songenv)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "limit_exceeded")
    assert row.spotify_calls == needed
    assert first.spotify_calls + (sent(songenv) - before) == needed


async def test_the_count_is_saved_with_each_stage_not_only_the_attempt_s_ending(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0009 section 8: a worker that dies mid-attempt loses at most its stage's count."""
    rider = await songenv.rider()
    job_id = await songenv.created(rider)
    before = sent(songenv)
    seen: list[tuple[int, int]] = []
    read = songenv.mowl._read  # pyright: ignore[reportPrivateUsage]

    def watching(req: urllib.request.Request, path: str, token: str) -> Any:
        if _TRACKS.match(path) and not seen:  # the pool_analyse stage, after the pool's writes
            seen.append((songenv.row(job_id).spotify_calls, sent(songenv) - before))
        return read(req, path, token)

    monkeypatch.setattr(songenv.mowl, "_read", watching)
    await songenv.run(job_id)
    [(stored, spent)] = seen
    assert stored == spent > 0


async def test_the_token_endpoint_counts_too(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    await songenv.spot.seed(make_context(rider.user_id), CLIENT_A, USER_A, expires_in=10)
    songenv.calls_per_job = 1  # the refresh is the job's first and only request
    before = len(songenv.spot.token_requests())
    job_id = await songenv.created(rider)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "limit_exceeded")
    assert len(songenv.spot.token_requests()) == before + 1
    assert songenv.api() == []  # nothing reached the API host


def mowl_rate_limits(songenv: SongEnv, monkeypatch: pytest.MonkeyPatch, times: int) -> list[int]:
    """The fake MOWL answers the pool's track list with a 429 ``times`` times."""
    left = [times]
    read = songenv.mowl._read  # pyright: ignore[reportPrivateUsage]

    def limited(req: urllib.request.Request, path: str, token: str) -> Any:
        if _TRACKS.match(path) and left[0] > 0:
            left[0] -= 1
            return songenv.mowl._answer(req, 429, {"Error": "slow down"})  # pyright: ignore[reportPrivateUsage]
        return read(req, path, token)

    monkeypatch.setattr(songenv.mowl, "_read", limited)
    return left


async def test_mowl_rate_limits_do_not_count_towards_quota_exhausted(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider = await songenv.rider()
    mowl_rate_limits(songenv, monkeypatch, 3)
    job_id = await songenv.created(rider)
    rows = [await songenv.run(job_id) for _ in range(3)]
    assert [r.state for r in rows] == ["queued", "queued", "failed"]
    # Its own category, the page's own wording, and no Spotify count.
    assert (rows[-1].error_category, rows[-1].spotify_rate_limits) == ("rate_limited", 0)
    assert rows[-1].spotify_failure is None
    assert await message(rider, job_id) is None


async def test_two_spotify_rate_limits_and_a_mowl_one_are_not_three_in_a_row(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider = await songenv.rider()
    songenv.fake.inject("rate_limited", method="POST", path=MINE, retry_after=30, times=2)
    job_id = await songenv.created(rider)
    rows = [await songenv.run(job_id) for _ in range(2)]
    assert [(r.state, r.spotify_rate_limits, r.spotify_failure) for r in rows] == [
        ("queued", 1, "rate_limited"),
        ("queued", 2, "rate_limited"),
    ]
    left = mowl_rate_limits(songenv, monkeypatch, 1)
    third = await songenv.run(job_id)
    assert left == [0]
    # Out of attempts on MOWL's rate limit: its own category, the row broken, no Spotify wording.
    assert (third.state, third.error_category, third.spotify_rate_limits) == (
        "failed",
        "rate_limited",
        0,
    )
    assert third.spotify_failure is None
    assert [r.spotify_retry_after for r in [*rows, third]] == [30, 30, None]  # the hint is gone
    assert await message(rider, job_id) is None


async def test_an_attempt_that_ends_well_resets_the_row(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    songenv.fake.inject("rate_limited", method="POST", path=MINE, retry_after=30, times=2)
    job_id = await songenv.created(rider)
    rows = [await songenv.run(job_id) for _ in range(2)]
    assert [r.spotify_rate_limits for r in rows] == [1, 2]
    row = await songenv.run(job_id)
    assert (row.state, row.spotify_rate_limits, row.spotify_failure, row.spotify_retry_after) == (
        "waiting_for_approval",
        0,
        None,
        None,
    )


async def test_quota_exhausted_settles_its_credit_like_any_failure(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    songenv.fake.inject("rate_limited", method="POST", path=MINE, retry_after=30, times=3)
    job_id = await songenv.created(rider)
    row = [await songenv.run(job_id) for _ in range(3)][-1]
    assert (row.state, row.error_category) == ("failed", "quota_exhausted")
    state, ledger = await settled(songenv, job_id)
    assert (state, ledger.committed, ledger.released) == ("released", [], [job_id])


async def test_spotify_not_answering_has_spotify_s_sentence_and_mowl_s_does_not(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    spotify_rider = await songenv.rider()
    songenv.fake.inject("refuse", method="POST", path=MINE, times=3)
    job_id = await songenv.created(spotify_rider)
    row = [await songenv.run(job_id) for _ in range(3)][-1]
    assert (row.state, row.error_category, row.spotify_failure) == (
        "failed",
        "upstream_unavailable",
        "upstream_unavailable",
    )
    assert await message(spotify_rider, job_id) == "Spotify is not answering."

    mowl_rider = await songenv.rider()
    read = songenv.mowl._read  # pyright: ignore[reportPrivateUsage]

    def down(req: urllib.request.Request, path: str, token: str) -> Any:
        if _TRACKS.match(path):
            return songenv.mowl._answer(req, 503, {"Error": "down"})  # pyright: ignore[reportPrivateUsage]
        return read(req, path, token)

    monkeypatch.setattr(songenv.mowl, "_read", down)
    other = await songenv.created(mowl_rider)
    row = [await songenv.run(other) for _ in range(3)][-1]
    assert (row.state, row.error_category, row.spotify_failure) == (
        "failed",
        "upstream_unavailable",
        None,
    )
    assert await message(mowl_rider, other) is None  # the page's own wording
