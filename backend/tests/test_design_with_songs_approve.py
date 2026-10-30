"""Approving a picked song list (ADR 0008 sections 5 and 7, ADR 0005 section 2; issue SJ1).

Every refusal starts nothing: no event, no claimable job, and no request at the fake Spotify, the
fake MOWL or the model (``counts``). The waiting job is made by the real kind against the fakes.
"""

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from classbeat import connections
from classbeat.jobs import queue, settle
from classbeat.jobs.credits import UnmeteredLedger
from classbeat.jobs.routes import credit_ledger
from ctx import make_context
from jobtools import new_job
from songtools import KIND, Rider, SongEnv
from spotifyfake import CLIENT_A, USER_B

pytestmark = pytest.mark.usefixtures("songenv")


def refused(response: Any, reason: str, status: int = 409) -> None:
    assert (response.status_code, response.json()) == (status, {"detail": {"reason": reason}})


async def nothing_started(songenv: SongEnv, job_id: UUID, before: tuple[Any, ...]) -> None:
    """The job still waits as it did, nothing is claimable, and no fake saw anything new."""
    kinds, counts = before
    row = songenv.row(job_id)
    assert (row.state, row.approved_at) == ("waiting_for_approval", None)
    assert songenv.kinds(job_id) == kinds
    assert await songenv.claim() is None
    assert songenv.counts() == counts


def snapshot(songenv: SongEnv, job_id: UUID) -> tuple[Any, ...]:
    return songenv.kinds(job_id), songenv.counts()


async def test_approve_once_queues_the_next_phase_and_a_replay_is_a_no_op(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    job_id, revision, digest = await songenv.waiting(rider)
    approved = await rider.approve(job_id, revision, digest)
    assert approved.status_code == 200, approved.text
    row = songenv.row(job_id)
    assert (row.state, row.attempt, row.approved_at is not None) == ("queued", 0, True)
    again = await rider.approve(job_id, revision, digest)
    assert (again.status_code, again.json()["state"]) == (200, "queued")
    assert songenv.kinds(job_id).count("approved") == 1
    # Phase 2 is issue SJ2: until it lands the approved phase fails closed (and releases credit).
    assert (await songenv.run(job_id)).state == "failed"
    after = await rider.approve(job_id, revision, digest)
    assert (after.status_code, after.json()["state"]) == (200, "failed")
    assert songenv.kinds(job_id).count("approved") == 1


async def test_a_double_click_and_a_second_tab_are_one_approval(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, revision, digest = await songenv.waiting(rider)
    tab = await songenv.spot.env.login(rider.user_id)
    other = Rider(
        type(rider.browser)(rider.browser.env, tab.token, rider.browser.client, rider.user_id),
        rider.spotify_connection_id,
        rider.mowl_connection_id,
    )
    answers = await asyncio.gather(
        rider.approve(job_id, revision, digest),
        rider.approve(job_id, revision, digest),
        other.approve(job_id, revision, digest),
    )
    assert [a.status_code for a in answers] == [200, 200, 200]
    assert songenv.kinds(job_id).count("approved") == 1
    assert songenv.row(job_id).state == "queued"


async def test_expired_superseded_and_hash_mismatch_start_nothing(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, revision, digest = await songenv.waiting(rider)
    before = snapshot(songenv, job_id)
    refused(await rider.approve(job_id, revision, "0" * 64), "hash_mismatch")
    class_id = songenv.row(job_id).class_id
    newer = uuid.uuid4()
    songenv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance) VALUES (:id, :o, :c, 99, 'song_list', "
        "CAST(:content AS jsonb), decode(repeat('ab', 32), 'hex'), '{}', '{}')",
        id=newer, o=rider.user_id, c=class_id, content=json.dumps({"songs": []}),
    )  # fmt: skip
    refused(await rider.approve(job_id, revision, digest), "superseded")
    songenv.sql("DELETE FROM class_revisions WHERE id = :i", i=newer)
    songenv.sql("UPDATE jobs SET expires_at = now() - interval '1 second' WHERE id = :i", i=job_id)
    refused(await rider.approve(job_id, revision, digest), "expired")
    await nothing_started(songenv, job_id, before)


SWAP = (
    "UPDATE class_revisions SET content = jsonb_set(jsonb_set(content, '{songs,1}', "
    "content #> '{songs,2}'), '{songs,2}', content #> '{songs,1}') WHERE id = :id"
)
RETITLE = (
    "UPDATE class_revisions SET content = jsonb_set(content, '{songs,0,title}', "
    "'\"Another title\"') WHERE id = :id"
)


@pytest.mark.parametrize("change", [SWAP, RETITLE], ids=["order", "title"])
async def test_any_change_to_the_stored_list_is_a_hash_mismatch(
    songenv: SongEnv, change: str
) -> None:
    rider = await songenv.rider()
    job_id, revision, digest = await songenv.waiting(rider)
    before = snapshot(songenv, job_id)
    # The stored row changed behind the app (the superuser; the app role cannot change it).
    songenv.sql(change, id=revision)
    refused(await rider.approve(job_id, revision, digest), "hash_mismatch")
    await nothing_started(songenv, job_id, before)


@pytest.mark.parametrize("provider", ["spotify", "mowl"])
async def test_a_disconnect_while_waiting_is_not_connected(songenv: SongEnv, provider: str) -> None:
    rider = await songenv.rider()
    job_id, revision, digest = await songenv.waiting(rider)
    assert await connections.disconnect(songenv.factory, make_context(rider.user_id), provider)
    before = snapshot(songenv, job_id)
    refused(await rider.approve(job_id, revision, digest), "not_connected")
    await nothing_started(songenv, job_id, before)


async def test_another_spotify_account_while_waiting_is_account_changed(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    job_id, revision, digest = await songenv.waiting(rider)
    # A reconnect as another Spotify user keeps the connection row (and its id).
    await songenv.spot.seed(make_context(rider.user_id), CLIENT_A, USER_B)
    assert songenv.sql(
        "SELECT id FROM connections WHERE owner_id = :o AND provider = 'spotify'", o=rider.user_id
    ).scalar_one() == rider.spotify_connection_id  # fmt: skip
    before = snapshot(songenv, job_id)
    refused(await rider.approve(job_id, revision, digest), "account_changed")
    await nothing_started(songenv, job_id, before)


async def test_the_per_user_limit_and_the_credit_seam(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, revision, digest = await songenv.waiting(rider)
    before = snapshot(songenv, job_id)
    planted = [
        new_job(
            songenv.admin,
            rider.user_id,
            state="running",
            attempt=1,
            leased_by="other-worker",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        for _ in range(songenv.spot.env.settings.jobs_per_user)
    ]
    refused(await rider.approve(job_id, revision, digest), "busy", 429)
    songenv.sql("DELETE FROM jobs WHERE id = ANY(:ids)", ids=planted)

    class NoCredit(UnmeteredLedger):
        async def allows(self, db: Any, ctx: Any, job_id: UUID) -> bool:
            return False

    songenv.spot.env.app.dependency_overrides[credit_ledger] = NoCredit
    try:
        refused(await rider.approve(job_id, revision, digest), "quota")
    finally:
        songenv.spot.env.app.dependency_overrides.pop(credit_ledger)
    await nothing_started(songenv, job_id, before)


async def test_another_users_job_or_revision_is_not_found(songenv: SongEnv) -> None:
    alice = await songenv.rider()
    job_a, revision_a, digest_a = await songenv.waiting(alice)
    bob = await songenv.rider(spotify_user=USER_B)
    job_b, revision_b, digest_b = await songenv.waiting(bob)
    before_a, before_b = snapshot(songenv, job_a), snapshot(songenv, job_b)
    refused(await bob.approve(job_a, revision_a, digest_a), "not_found", 404)
    refused(await alice.approve(job_a, revision_b, digest_b), "not_found", 404)
    refused(await alice.approve(job_a, uuid.uuid4(), digest_a), "not_found", 404)
    await nothing_started(songenv, job_a, before_a)
    await nothing_started(songenv, job_b, before_b)


class Releases(UnmeteredLedger):
    def __init__(self) -> None:
        self.released: list[UUID] = []

    async def release(self, db: Any, ctx: Any, job_id: UUID) -> None:
        self.released.append(job_id)


async def test_an_expired_list_makes_no_call_and_releases_its_credit_once(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    job_id, revision, digest = await songenv.waiting(rider)
    counts = songenv.counts()
    assert songenv.row(job_id).credit_state == "reserved"
    songenv.sql("UPDATE jobs SET expires_at = now() - interval '1 second' WHERE id = :i", i=job_id)
    assert songenv.sql("SELECT public.queue_expire(10)").scalar_one() == 1
    ledger = Releases()
    await settle(songenv.factory, ledger)
    await settle(songenv.factory, ledger)
    row = songenv.row(job_id)
    assert (row.state, row.credit_state) == ("expired", "released")
    assert ledger.released == [job_id]
    refused(await rider.approve(job_id, revision, digest), "expired")
    assert await songenv.claim() is None
    # No model, MOWL or Spotify call after the wait began, the pool playlist included.
    assert songenv.counts() == counts


async def test_a_waiting_job_holds_no_slot_and_survives_a_restart(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, _, _ = await songenv.waiting(rider)
    before = songenv.row(job_id)
    counts = songenv.counts()
    # A restart: the reaper and the expiry sweep run, a fresh worker claims; nothing changes.
    assert await queue.reap(songenv.factory) == 0
    assert songenv.sql("SELECT public.queue_expire(10)").scalar_one() == 0
    assert await songenv.claim() is None
    after = songenv.row(job_id)
    assert (after.state, after.lease_version, after.next_seq, after.expires_at) == (
        before.state, before.lease_version, before.next_seq, before.expires_at,
    )  # fmt: skip
    assert songenv.counts() == counts
    # The per-user limit does not count it: with one other job running a new pick is admitted.
    new_job(
        songenv.admin,
        rider.user_id,
        state="running",
        attempt=1,
        leased_by="other-worker",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    assert songenv.spot.env.settings.jobs_per_user == 2
    created = await rider.create()
    assert created.status_code == 201, created.text
    assert created.json()["kind"] == KIND


async def test_a_cancel_while_waiting_is_immediate_and_makes_no_call(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, revision, digest = await songenv.waiting(rider)
    counts = songenv.counts()
    started = time.monotonic()
    cancelled = await rider.browser.post(f"/api/jobs/{job_id}/cancel")
    assert (cancelled.status_code, cancelled.json()["state"]) == (200, "cancelled")
    assert time.monotonic() - started < 5
    refused(await rider.approve(job_id, revision, digest), "not_waiting")
    assert songenv.counts() == counts
