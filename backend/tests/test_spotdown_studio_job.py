"""SD-P5 (#1302, #1353, ADR 0040 sections 5 to 9 and its amendment "spotdown is the only analysis
path"): studio tracks analysed by spotdown, as a job on the fakes: the fake gateway and the fake
spotdown (``spotdownfake``, in process). Never the network: the autouse netguard refuses any other
connection.

- spotdown is the source of each downloaded track's map (``exact``, the played bytes), stored with
  its provenance; the owner's cache by reference, then by sha256, then spotdown's lookup by sha256
  in the studio's shared scope (#1338: every owner reuses the analysis of the same file) come
  before any upload; the audio is gone after the job;
- spotdown not installed, or without the ``upload`` source: the job ends ``analysis_unavailable``
  and nothing is sent (the table of every ending is ``test_spotdown_failure_kinds``);
- a failure ends the job; nothing is written for the track; the wait budget and the outage flag
  are the job's over its runs;
- writes are fenced (a lost lease stores nothing); account deletion deletes the owner's scope in
  spotdown and leaves the shared studio scope.
"""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from mowl import track

from classbeat.account import erase
from classbeat.design import track_spotdown
from classbeat.spotdown import scope_outbox, studio_policy, studio_tracks_config
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.scope_calc import cache_scope, shared_scope
from classbeat.spotdown.studio_map_model import STUDIO_POLICY_VERSIONS, StudioMap, studio_map
from ctx import make_context
from jobtools import new_job
from spotdownfake import FakeSpotdown, spotdown
from spotdownstudioharness import (
    AUDIO,
    ONE,
    OTHER_AUDIO,
    SHA,
    TWO,
    Harness,
    env,
    harness,
    last_result,
    work,
)

__all__ = ["env", "harness", "spotdown", "work"]  # the fixtures

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default")
THREE = "trk-three-03"  # the same bytes as ONE under another reference
KEY = bytes(range(32))
SHARED = shared_scope(KEY, "studio")  # #1338: every owner's studio analyses
OTHER_SHA = hashlib.sha256(OTHER_AUDIO).hexdigest()
UPLOAD_ON = studio_tracks_config.TracksConfig(KEY)


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    async def pause(*_args: object) -> None:
        return None

    monkeypatch.setattr(track_spotdown, "pause", pause)
    studio_tracks_config.configure(UPLOAD_ON)
    yield
    studio_tracks_config.configure(None)


def rows(harness: Harness, owner: UUID) -> list[Any]:
    return harness.ops.sql(
        "SELECT reference, sha256, map, analyser, provenance FROM swedebeat_track_maps "
        "WHERE owner_id = :o ORDER BY reference, analyser",
        o=owner,
    ).all()


def only_analysis(spotdown: FakeSpotdown) -> str:
    (analysis_id,) = spotdown.analyses
    return analysis_id


def downloads(harness: Harness, owner: UUID) -> int:
    return harness.ops.sql(
        "SELECT count(*) FROM swedebeat_track_downloads WHERE owner_id = :o", o=owner
    ).scalar_one()


def sent(fake: FakeSpotdown, path: str) -> list[Any]:
    return [s for s in fake.seen if s.path == path]


def expected_map(fake: FakeSpotdown, scope: str, sha: str, reference: str) -> dict[str, object]:
    analysis_id = fake._find(scope, f"sha256:{sha}")  # pyright: ignore[reportPrivateUsage]
    assert analysis_id is not None
    mapped = studio_map(fake.analysis(analysis_id), reference, sha, track.TRACK_VERSION)
    assert isinstance(mapped, StudioMap)
    return mapped.track_map


async def on_owner(harness: Harness) -> UUID:
    return await harness.user()


# --- spotdown not installed ---------------------------------------------------------------------


async def test_not_installed_the_job_is_unavailable_and_sends_nothing(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    studio_tracks_config.configure(None)
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("failed", "analysis_unavailable")
    assert spotdown.seen == []
    assert rows(harness, owner) == []


async def test_a_cached_map_needs_no_configuration(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    owner = await on_owner(harness)
    await harness.run(owner, [ONE])
    studio_tracks_config.configure(None)
    seen = len(spotdown.seen)
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("completed", None)
    assert len(spotdown.seen) == seen
    assert [t[3] for t in last_result(harness)] == [True]


# --- spotdown is the source ---------------------------------------------------------------------
async def test_spotdown_gives_each_map_with_its_provenance(
    harness: Harness, spotdown: FakeSpotdown, work: Path
) -> None:
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE, TWO])
    assert (row.state, row.error_category) == ("completed", None)
    assert not any(work.iterdir())  # no audio left  # noqa: ASYNC240
    scope = SHARED
    lookups, uploads = sent(spotdown, "/v2/analyses/lookup"), sent(spotdown, "/v2/uploads")
    assert [hashlib.sha256(u.body).hexdigest() for u in uploads] == [SHA, OTHER_SHA]
    assert len(lookups) == 2
    assert all(f"cache_scope={scope}" in s.query for s in lookups)
    assert all(f"cache_scope={scope}" in s.query for s in uploads)
    assert spotdown.seen.index(lookups[0]) < spotdown.seen.index(uploads[0])  # lookup first
    body = b"".join(s.body + s.query.encode() for s in spotdown.seen if s.path != "/v2/uploads")
    assert ONE.encode() not in body  # no reference leaves
    assert TWO.encode() not in body
    stored = rows(harness, owner)
    assert [(r.reference, r.analyser) for r in stored] == [(ONE, "spotdown"), (TWO, "spotdown")]
    for r, sha in zip(stored, (SHA, OTHER_SHA), strict=True):
        assert bytes(r.sha256).hex() == sha
        assert r.map == expected_map(spotdown, scope, sha, r.reference)
        assert r.provenance["timing"]["class"] == "exact"
        assert r.provenance["timing"]["reason"] == "played_recording"
        assert r.provenance["audio"]["sha256"] == sha
        assert r.provenance["policy_versions"] == dict(STUDIO_POLICY_VERSIONS)
        assert r.provenance["source"] == {"kind": "upload", "ref": None}
        text = json.dumps(r.provenance)
        for secret in (
            ONE,
            TWO,
            scope,
            cache_scope(KEY, owner),
            "studio-",
            "cache_scope",
            "Bearer",
        ):
            assert secret not in text
    result = last_result(harness)
    assert [(t[0], t[2]) for t in result] == [(r.reference, r.map) for r in stored]


async def test_a_cached_map_needs_no_download_and_no_spotdown_call(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    owner = await on_owner(harness)
    await harness.run(owner, [ONE])
    seen, fetched = len(spotdown.seen), downloads(harness, owner)
    await harness.run(owner, [ONE])
    assert (len(spotdown.seen), downloads(harness, owner)) == (seen, fetched)
    assert [t[3] for t in last_result(harness)] == [True]  # from the cache


async def test_the_same_bytes_under_another_reference_are_neither_looked_up_nor_uploaded(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    harness.gateway.add_audio(THREE, AUDIO)
    owner = await on_owner(harness)
    await harness.run(owner, [ONE])
    seen = len(spotdown.seen)
    await harness.run(owner, [THREE])
    assert len(spotdown.seen) == seen  # the owner's own cache by sha256
    one, three = rows(harness, owner)
    assert (three.reference, three.analyser, three.map) == (THREE, "spotdown", one.map)
    assert three.provenance == one.provenance


async def test_an_analysis_in_the_shared_scope_is_read_without_an_upload(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    owner = await on_owner(harness)
    spotdown.add_analysis(SHARED, f"sha256:{SHA}")
    await harness.run(owner, [ONE])
    assert sent(spotdown, "/v2/uploads") == []
    assert [r.analyser for r in rows(harness, owner)] == ["spotdown"]


async def test_a_second_owner_reuses_the_studio_analysis_without_an_upload(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    """#1338 (owner answer to #1297): a studio track is the same file for every user, so its
    analysis is shared; no owner's own scope is sent for it, and each owner keeps their own row."""
    first, second = await harness.user(), await harness.user()
    await harness.run(first, [ONE])
    before = len(spotdown.seen)
    await harness.run(second, [ONE])
    later = spotdown.seen[before:]
    assert [s.path for s in later] == [
        "/v2/analyses/lookup",
        f"/v2/analyses/{only_analysis(spotdown)}",
    ]
    assert all(f"cache_scope={SHARED}" in s.query for s in later)
    for s in later:
        for owner in (first, second):
            assert cache_scope(KEY, owner) not in s.query + s.body.decode("latin-1")
    assert [r.analyser for r in rows(harness, second)] == ["spotdown"]  # their own row
    assert rows(harness, second)[0].map == rows(harness, first)[0].map


# --- a failure ends the job: no map is stored for the track -------------------------------------


def _injected(status: int, **headers: str) -> dict[str, list[httpx.Response]]:
    return {"/v2/analyses/lookup": [httpx.Response(status, headers=headers)] * 4}


@pytest.mark.parametrize(
    ("setup", "uploads", "category"),
    [
        ({"connect_error": True}, 0, "analysis_unavailable"),  # unreachable
        ({"inject": _injected(503)}, 0, "analysis_unavailable"),  # server error: unreachable
        ({"inject": _injected(401)}, 0, "analysis_unavailable"),  # a refused key: unreachable
        ({"end": "processing_failed:decode_error"}, 1, "analysis_failed"),  # deterministic
        ({"end": "processing_failed:out_of_memory"}, 1, "analysis_failed"),
        ({"end": "processing_failed:timeout"}, 2, "analysis_failed"),  # one retry first
        ({"end": "provider_error"}, 2, "analysis_unavailable"),  # one retry, then an outage
        ({"end": "alignment_failed"}, 1, "analysis_failed"),
        ({"end": "source_unavailable"}, 1, "analysis_failed"),
    ],
)
async def test_a_spotdown_failure_ends_the_job_with_its_category(
    harness: Harness, spotdown: FakeSpotdown, setup: dict[str, Any], uploads: int, category: str
) -> None:
    for name, value in setup.items():
        setattr(spotdown, name, value)
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE, TWO])
    assert (row.state, row.error_category) == ("failed", category)
    assert len(sent(spotdown, "/v2/uploads")) == uploads  # the second track is never tried
    assert rows(harness, owner) == []


async def test_a_rate_limited_job_waits_within_the_budget_then_is_unavailable(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(studio_policy, "WAIT_SECONDS", 0.3)
    spotdown.end = "rate_limited"
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("failed", "analysis_unavailable")
    assert len(sent(spotdown, "/v2/uploads")) == 1  # the wait is not an attempt


async def test_a_rate_limited_call_is_tried_again_and_is_not_an_attempt(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    spotdown.inject["/v2/analyses/lookup"] = [httpx.Response(429, headers={"Retry-After": "2"})]
    owner = await on_owner(harness)
    await harness.run(owner, [ONE])
    assert len(sent(spotdown, "/v2/analyses/lookup")) == 2
    assert [r.analyser for r in rows(harness, owner)] == ["spotdown"]


async def test_a_spent_budget_calls_nothing_and_is_unavailable(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(studio_policy, "WAIT_SECONDS", 0.0)
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE, TWO])
    assert (row.state, row.error_category) == ("failed", "analysis_unavailable")
    assert spotdown.seen == []
    assert rows(harness, owner) == []


async def test_a_document_of_other_bytes_is_never_used(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = spotdown.analysis

    def other_bytes(analysis_id: str) -> dict[str, Any]:
        document = real(analysis_id)
        document["provenance"]["audio"]["sha256"] = "e" * 64
        return document

    monkeypatch.setattr(spotdown, "analysis", other_bytes)
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("failed", "analysis_failed")  # not exact
    assert rows(harness, owner) == []


async def test_the_tracks_before_a_failing_one_keep_their_maps(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    real, starts = spotdown._start, [0]  # pyright: ignore[reportPrivateUsage]

    def second_fails(scope: str, body: bytes) -> httpx.Response:
        starts[0] += 1
        spotdown.end = "processing_failed:decode_error" if starts[0] == 2 else None
        return real(scope, body)

    monkeypatch.setattr(spotdown, "_start", second_fails)
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE, TWO])
    assert (row.state, row.error_category) == ("failed", "analysis_failed")
    assert [(r.reference, r.analyser) for r in rows(harness, owner)] == [(ONE, "spotdown")]


async def test_an_error_in_the_spotdown_code_is_internal_and_not_swallowed(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object) -> None:
        raise RuntimeError("a bug in the map")

    monkeypatch.setattr(track_spotdown, "studio_map", broken)
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("failed", "internal")
    assert rows(harness, owner) == []


# --- the cache's rule, fenced writes and deletion -----------------------------------------------


async def test_a_row_of_other_policy_versions_is_analysed_again(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    owner = await on_owner(harness)
    await harness.run(owner, [ONE])
    harness.ops.sql(
        "UPDATE swedebeat_track_maps SET provenance = jsonb_set(provenance, "
        "'{policy_versions,spotdown_map_policy}', '\"0\"') WHERE owner_id = :o",
        o=owner,
    )
    fetched = downloads(harness, owner)
    await harness.run(owner, [ONE])
    assert downloads(harness, owner) == fetched + 1  # a miss: downloaded again
    assert len(sent(spotdown, "/v2/uploads")) == 1  # spotdown's lookup had it
    [stored] = rows(harness, owner)
    assert stored.provenance["policy_versions"] == dict(STUDIO_POLICY_VERSIONS)


async def test_a_lost_lease_while_waiting_stops_the_run_and_stores_nothing(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch, work: Path
) -> None:
    real = SpotdownClient.poll

    async def stolen(self: SpotdownClient, job_id: str) -> Any:
        harness.ops.sql("UPDATE jobs SET lease_version = lease_version + 1 WHERE state = 'running'")
        return await real(self, job_id)

    monkeypatch.setattr(SpotdownClient, "poll", stolen)
    owner = await on_owner(harness)
    await harness.run(owner, [ONE])
    assert rows(harness, owner) == []  # nothing written after the lease was lost
    assert not any(work.iterdir())  # noqa: ASYNC240


async def test_account_deletion_deletes_the_owners_scope_and_keeps_the_shared_studio_one(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    """#1338: the shared studio analysis holds a catalog file's analysis and no user data, so one
    account's deletion leaves it for the other owners; the owner's own scope is still deleted."""
    owner, other = await harness.user(), await harness.user()
    await harness.run(owner, [ONE])
    await harness.run(other, [ONE])
    assert [s for s, _by in spotdown.analyses.values()] == [SHARED]  # analysed once
    assert await erase.delete_row(harness.ops.factory, make_context(owner), KEY)
    assert await scope_outbox.drain(harness.ops.factory) == 1
    assert [s for s, _by in spotdown.analyses.values()] == [SHARED]
    mine = cache_scope(KEY, owner)
    assert [s.path for s in spotdown.seen if s.method == "DELETE"] == [f"/v2/scopes/{mine}"]


# --- the bounds hold per attempt of the job, over its runs (#1302 review B1, #1353) -------------


def budget(harness: Harness, owner: UUID) -> list[Any]:
    return harness.ops.sql(
        "SELECT job_id, outage FROM spotdown_studio_budgets WHERE owner_id = :o", o=owner
    ).all()


async def two_runs(harness: Harness, owner: UUID, monkeypatch: pytest.MonkeyPatch) -> Any:
    """One job of ONE and TWO, one download per run: the second run is a requeue."""
    from classbeat.design import track_analysis as analysis
    from jobtools import new_job

    monkeypatch.setattr(analysis, "SLOT_BATCH", 1)
    body = json.dumps({"references": [ONE, TWO]})
    job_id = new_job(harness.ops.admin, owner, input=body, max_attempts=1)
    await harness.ops.run_next()
    assert harness.ops.row(job_id).state == "queued"  # requeued for the rest
    await harness.ops.run_next()
    return harness.ops.row(job_id)


async def test_an_outage_holds_for_the_whole_attempt_over_its_runs(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first run ends ``analysis_unavailable`` on an outage; a requeue that keeps the budget (a
    lease lost, say) is flagged and calls spotdown no more."""
    spotdown.connect_error = True
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE, TWO])
    assert (row.state, row.error_category) == ("failed", "analysis_unavailable")
    assert [s.path for s in spotdown.seen] == ["/v2/analyses/lookup"]
    assert [b.outage for b in budget(harness, owner)] == [True]
    # A later run of the same attempt (a requeue after a lost lease keeps the budget) is flagged.
    spotdown.connect_error = False  # it came back, but the attempt is flagged
    second = await harness.user()
    job_id = new_job(
        harness.ops.admin, second, input=json.dumps({"references": [ONE]}), max_attempts=1
    )
    harness.ops.sql(
        "INSERT INTO spotdown_studio_budgets (owner_id, job_id, outage) VALUES (:o, :j, true)",
        o=second, j=job_id,
    )  # fmt: skip
    await harness.ops.run_next()
    flagged = harness.ops.row(job_id)
    assert (flagged.state, flagged.error_category) == ("failed", "analysis_unavailable")
    assert [s.path for s in spotdown.seen] == ["/v2/analyses/lookup"]  # nothing more was sent


async def test_the_wait_budget_is_the_jobs_over_its_runs(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await on_owner(harness)
    real = SpotdownClient.get_analysis

    async def then_spent(self: SpotdownClient, *args: Any, **kwargs: Any) -> Any:
        harness.ops.sql(  # the first run's wait used up the job's 600 s
            "UPDATE spotdown_studio_budgets SET started_at = now() - interval '11 minutes' "
            "WHERE owner_id = :o",
            o=owner,
        )
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(SpotdownClient, "get_analysis", then_spent)
    row = await two_runs(harness, owner, monkeypatch)
    assert (row.state, row.error_category) == ("failed", "analysis_unavailable")
    assert len(sent(spotdown, "/v2/analyses/lookup")) == 1  # nothing in the second run
    assert [r.analyser for r in rows(harness, owner)] == ["spotdown"]
    assert [b.outage for b in budget(harness, owner)] == [False]


async def test_a_rate_limited_start_keeps_the_same_upload(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    spotdown.inject["/v2/analyses"] = [httpx.Response(429, headers={"Retry-After": "2"})]
    owner = await on_owner(harness)
    await harness.run(owner, [ONE])
    uploads = sent(spotdown, "/v2/uploads")
    assert len({u.headers["idempotency-key"] for u in uploads}) == 1  # the same upload, again
    starts = sent(spotdown, "/v2/analyses")
    assert len({s.headers["idempotency-key"] for s in starts}) == 1
    assert [r.analyser for r in rows(harness, owner)] == ["spotdown"]


async def test_a_retry_after_a_timeout_is_a_new_attempt_that_can_succeed(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    real, starts = spotdown._start, [0]  # pyright: ignore[reportPrivateUsage]

    def first_times_out(scope: str, body: bytes) -> httpx.Response:
        starts[0] += 1
        spotdown.end = "processing_failed:timeout" if starts[0] == 1 else None
        return real(scope, body)

    monkeypatch.setattr(spotdown, "_start", first_times_out)
    owner = await on_owner(harness)
    await harness.run(owner, [ONE])
    uploads = sent(spotdown, "/v2/uploads")
    assert len({u.headers["idempotency-key"] for u in uploads}) == 2  # a new attempt's keys
    assert [r.analyser for r in rows(harness, owner)] == ["spotdown"]


async def test_the_by_sha256_read_filters_on_the_owner_itself(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    """Under the superuser (no row security), another owner's row of the same bytes is no hit."""
    from classbeat.design.track_cache import _BY_SHA256  # pyright: ignore[reportPrivateUsage]

    first = await on_owner(harness)
    await harness.run(first, [ONE])
    other = await harness.user()
    params = {"owner_id": other, "version": track.TRACK_VERSION, "sha256": bytes.fromhex(SHA)}
    found = harness.ops.sql(str(_BY_SHA256), **params, days=30).all()
    assert found == []
    assert len(harness.ops.sql(str(_BY_SHA256), **(params | {"owner_id": first}), days=30).all())
