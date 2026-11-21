"""SD-P5 (#1302, ADR 0040 sections 5 to 9): studio tracks analysed by spotdown, as a job on the
fakes: the fake gateway, the fake spotdown (``spotdownfake``, in process) and the real sandboxed
child on generated audio for the local path. Never the network: the autouse netguard refuses any
other connection.

- off (the default), an owner not on the allowlist, or spotdown not installed: nothing is sent,
  every map is the local one, as today;
- on: spotdown is the source of each downloaded track's map (``exact``, the played bytes), stored
  with its provenance; the owner's cache by reference, then by sha256, then spotdown's lookup by
  sha256 in the owner's scope come before any upload; the audio is gone after the job;
- every spotdown failure and outage falls back to the local analysis of that track only, and the
  result is then today's;
- writes are fenced (a lost lease stores nothing); account deletion deletes the scope in spotdown.
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
from classbeat.design import track_analysis, track_spotdown
from classbeat.jobs import JobRun
from classbeat.spotdown import scope_outbox, shadow_config, studio_policy, studio_tracks_config
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.provenance_model import POLICY_VERSIONS
from classbeat.spotdown.scope_calc import cache_scope
from classbeat.spotdown.studio_map_model import StudioMap, studio_map
from ctx import make_context
from mediafake import kick_wav_bytes
from spotdownfake import FakeSpotdown, spotdown
from test_spotdown_shadow_job import (
    AUDIO,
    ONE,
    OTHER_AUDIO,
    SHA,
    TWO,
    Harness,
    baseline,
    env,
    harness,
    last_result,
    work,
)

__all__ = ["env", "harness", "spotdown", "work"]  # the fixtures

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default")
THREE = "trk-three-03"  # the same bytes as ONE under another reference
KEY = bytes(range(32))
OTHER_SHA = hashlib.sha256(OTHER_AUDIO).hexdigest()


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    async def pause(*_args: object) -> None:
        return None

    from classbeat.design import track_shadow
    from classbeat.swedebeat_match import gateway_match

    monkeypatch.setattr(gateway_match, "_pause", pause)
    monkeypatch.setattr(track_shadow, "_pause", pause)
    monkeypatch.setattr(track_spotdown, "pause", pause)
    yield
    studio_tracks_config.configure(None)
    shadow_config.configure(None)


@pytest.fixture
def local_runs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each local analysis (the real sandboxed child still runs)."""
    seen: list[str] = []
    real = track_analysis.analyse_audio

    async def counted(run: JobRun, audio: Path, **kwargs: Any) -> Any:
        seen.append(audio.name)
        return await real(run, audio, **kwargs)

    monkeypatch.setattr(track_analysis, "analyse_audio", counted)
    return seen


def turn_on(*owners: UUID) -> None:
    studio_tracks_config.configure(
        studio_tracks_config.TracksConfig(
            "allowlist", frozenset(owners), frozenset({"upload"}), KEY
        )
    )


def rows(harness: Harness, owner: UUID) -> list[Any]:
    return harness.ops.sql(
        "SELECT reference, sha256, map, analyser, provenance FROM swedebeat_track_maps "
        "WHERE owner_id = :o ORDER BY reference, analyser",
        o=owner,
    ).all()


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
    owner = await harness.user()
    turn_on(owner)
    return owner


# --- off: today's analysis -----------------------------------------------------------------------


async def test_off_by_default_nothing_is_sent_and_every_map_is_local(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    owner = await harness.user()
    row = await harness.run(owner, [ONE, TWO])
    assert (row.state, row.error_category) == ("completed", None)
    assert spotdown.seen == []
    assert [(r.analyser, r.provenance) for r in rows(harness, owner)] == [("local", None)] * 2


async def test_an_owner_not_on_the_allowlist_is_analysed_locally(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    listed, owner = await harness.user(), await harness.user()
    turn_on(listed)
    await harness.run(owner, [ONE])
    assert spotdown.seen == []
    assert [r.analyser for r in rows(harness, owner)] == ["local"]


async def test_on_without_spotdown_installed_is_local(harness: Harness) -> None:
    owner = await on_owner(harness)  # no ``spotdown`` fixture: CLASSBEAT_SPOTDOWN_URL empty
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("completed", None)
    assert [r.analyser for r in rows(harness, owner)] == ["local"]


# --- on: spotdown is the source ----------------------------------------------------------------


async def test_spotdown_gives_each_map_with_its_provenance_and_no_local_run(
    harness: Harness, spotdown: FakeSpotdown, local_runs: list[str], work: Path
) -> None:
    shadow_config.configure(shadow_config.ShadowConfig(True, frozenset({"upload"}), KEY))
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE, TWO])
    assert (row.state, row.error_category) == ("completed", None)
    assert local_runs == []  # spotdown analysed both
    assert not any(work.iterdir())  # no audio left  # noqa: ASYNC240
    scope = cache_scope(KEY, owner)
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
        assert r.provenance["policy_versions"] == dict(POLICY_VERSIONS)
        assert r.provenance["source"] == {"kind": "upload", "ref": None}
        text = json.dumps(r.provenance)
        for secret in (ONE, TWO, scope, "studio-", "cache_scope", "Bearer"):
            assert secret not in text
    result = last_result(harness)
    assert [(t[0], t[2]) for t in result] == [(r.reference, r.map) for r in stored]
    assert harness.shadows(owner) == []  # no shadow while spotdown is the source


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
    harness: Harness, spotdown: FakeSpotdown, local_runs: list[str]
) -> None:
    harness.gateway.add_audio(THREE, AUDIO)
    owner = await on_owner(harness)
    await harness.run(owner, [ONE])
    seen = len(spotdown.seen)
    await harness.run(owner, [THREE])
    assert len(spotdown.seen) == seen  # the owner's own cache by sha256
    assert local_runs == []
    one, three = rows(harness, owner)
    assert (three.reference, three.analyser, three.map) == (THREE, "spotdown", one.map)
    assert three.provenance == one.provenance


async def test_an_analysis_in_the_owners_scope_is_read_without_an_upload(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    owner = await on_owner(harness)
    spotdown.add_analysis(cache_scope(KEY, owner), f"sha256:{SHA}")
    await harness.run(owner, [ONE])
    assert sent(spotdown, "/v2/uploads") == []
    assert [r.analyser for r in rows(harness, owner)] == ["spotdown"]


async def test_another_owners_scope_is_never_read_or_written(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    first, second = await harness.user(), await harness.user()
    turn_on(first, second)
    await harness.run(first, [ONE])
    before = len(spotdown.seen)
    await harness.run(second, [ONE])
    mine = cache_scope(KEY, second)
    later = spotdown.seen[before:]
    assert [s.path for s in later if s.path == "/v2/uploads"] == ["/v2/uploads"]  # again
    for s in later:
        assert cache_scope(KEY, first) not in s.query + s.body.decode("latin-1")
    assert all(mine in s.query for s in later if s.path in ("/v2/uploads", "/v2/analyses/lookup"))
    assert [r.analyser for r in rows(harness, second)] == ["spotdown"]


# --- every failure falls back to the local analysis of that track --------------------------------


def _injected(status: int, **headers: str) -> dict[str, list[httpx.Response]]:
    return {"/v2/analyses/lookup": [httpx.Response(status, headers=headers)] * 4}


@pytest.mark.parametrize(
    ("setup", "uploads"),
    [
        ({"connect_error": True}, 0),  # unreachable
        ({"inject": _injected(503)}, 0),  # server error: unreachable
        ({"inject": _injected(401)}, 0),  # a refused key: unreachable
        ({"end": "processing_failed:decode_error"}, 1),  # deterministic: no retry
        ({"end": "processing_failed:out_of_memory"}, 1),
        ({"end": "processing_failed:timeout"}, 2),  # one retry after a timeout
        ({"end": "provider_error"}, 2),  # one retry
        ({"end": "alignment_failed"}, 1),
        ({"end": "source_unavailable"}, 1),
    ],
)
async def test_a_spotdown_failure_gives_todays_map(
    harness: Harness, spotdown: FakeSpotdown, setup: dict[str, Any], uploads: int
) -> None:
    off_result, off_maps = await baseline(harness, [ONE])
    for name, value in setup.items():
        setattr(spotdown, name, value)
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("completed", None)
    assert last_result(harness) == off_result  # exactly today's analysis
    assert [(r.reference, bytes(r.sha256), r.map) for r in rows(harness, owner)] == off_maps
    assert [(r.analyser, r.provenance) for r in rows(harness, owner)] == [("local", None)]
    assert len(sent(spotdown, "/v2/uploads")) == uploads


async def test_a_rate_limited_job_waits_within_the_budget_then_falls_back(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(studio_policy, "WAIT_SECONDS", 0.3)
    off_result, _maps = await baseline(harness, [ONE])
    spotdown.end = "rate_limited"
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE])
    assert row.state == "completed"
    assert last_result(harness) == off_result
    assert len(sent(spotdown, "/v2/uploads")) == 1  # the wait is not an attempt


async def test_a_rate_limited_call_is_tried_again_and_is_not_an_attempt(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    spotdown.inject["/v2/analyses/lookup"] = [httpx.Response(429, headers={"Retry-After": "2"})]
    owner = await on_owner(harness)
    await harness.run(owner, [ONE])
    assert len(sent(spotdown, "/v2/analyses/lookup")) == 2
    assert [r.analyser for r in rows(harness, owner)] == ["spotdown"]


async def test_a_spent_budget_calls_nothing_and_is_local(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(studio_policy, "WAIT_SECONDS", 0.0)
    owner = await on_owner(harness)
    await harness.run(owner, [ONE, TWO])
    assert spotdown.seen == []
    assert [r.analyser for r in rows(harness, owner)] == ["local", "local"]


async def test_a_document_of_other_bytes_is_never_used(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = spotdown.analysis

    def other_bytes(analysis_id: str) -> dict[str, Any]:
        document = real(analysis_id)
        document["provenance"]["audio"]["sha256"] = "e" * 64
        return document

    monkeypatch.setattr(spotdown, "analysis", other_bytes)
    off_result, _maps = await baseline(harness, [ONE])
    owner = await on_owner(harness)
    await harness.run(owner, [ONE])
    assert last_result(harness) == off_result  # not exact: today's map
    assert [r.analyser for r in rows(harness, owner)] == ["local"]


async def test_an_outage_stops_calling_spotdown_for_the_rest_of_the_run(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    spotdown.connect_error = True
    owner = await on_owner(harness)
    await harness.run(owner, [ONE, TWO])
    assert [s.path for s in spotdown.seen] == ["/v2/analyses/lookup"]  # the first track only
    assert [r.analyser for r in rows(harness, owner)] == ["local", "local"]


async def test_one_failing_track_changes_only_that_track(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    off_result, _maps = await baseline(harness, [ONE, TWO])
    real, starts = spotdown._start, [0]  # pyright: ignore[reportPrivateUsage]

    def first_fails(scope: str, body: bytes) -> httpx.Response:
        starts[0] += 1
        spotdown.end = "processing_failed:decode_error" if starts[0] == 1 else None
        return real(scope, body)

    monkeypatch.setattr(spotdown, "_start", first_fails)
    owner = await on_owner(harness)
    await harness.run(owner, [ONE, TWO])
    one, two = last_result(harness)
    assert one == off_result[0]  # today's map for the failing track
    assert (one[0], two[0]) == (ONE, TWO)
    assert [r.analyser for r in rows(harness, owner)] == ["local", "spotdown"]
    assert two[2] == expected_map(spotdown, cache_scope(KEY, owner), OTHER_SHA, TWO)


async def test_an_error_in_the_spotdown_code_falls_back(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object) -> None:
        raise RuntimeError("a bug in the map")

    monkeypatch.setattr(track_spotdown, "studio_map", broken)
    off_result, _maps = await baseline(harness, [ONE])
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("completed", None)
    assert last_result(harness) == off_result


# --- switching it off, the cache's rule, fenced writes and deletion ------------------------------


async def test_switched_off_the_track_is_analysed_locally_beside_the_spotdown_row(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    """The rollback lever: the next job is today's, and the spotdown row a class may have been
    designed on is kept, never replaced by the local map (#1302 review B2)."""
    off_result, off_maps = await baseline(harness, [ONE])
    owner = await on_owner(harness)
    await harness.run(owner, [ONE])
    [designed_on] = rows(harness, owner)
    assert designed_on.analyser == "spotdown"
    studio_tracks_config.configure(None)
    seen = len(spotdown.seen)
    await harness.run(owner, [ONE])
    assert len(spotdown.seen) == seen
    assert last_result(harness) == off_result
    local, kept = sorted(rows(harness, owner), key=lambda r: r.analyser)
    assert [(local.reference, bytes(local.sha256), local.map)] == off_maps
    assert (local.analyser, local.provenance) == ("local", None)
    assert (kept.analyser, kept.map, kept.provenance) == (
        "spotdown",
        designed_on.map,
        designed_on.provenance,
    )


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
    assert stored.provenance["policy_versions"] == dict(POLICY_VERSIONS)


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


async def test_account_deletion_deletes_the_owners_scope_in_spotdown(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    owner, other = await harness.user(), await harness.user()
    turn_on(owner, other)
    await harness.run(owner, [ONE])
    await harness.run(other, [ONE])
    mine, theirs = cache_scope(KEY, owner), cache_scope(KEY, other)
    assert {s for s, _by in spotdown.analyses.values()} == {mine, theirs}
    assert await erase.delete_row(harness.ops.factory, make_context(owner), KEY)
    assert await scope_outbox.drain(harness.ops.factory) == 1
    assert {s for s, _by in spotdown.analyses.values()} == {theirs}
    assert [s.path for s in spotdown.seen if s.method == "DELETE"] == [f"/v2/scopes/{mine}"]


# --- the bounds hold per class, over every run of the job (#1302 review B1) --------------------


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


async def test_an_outage_holds_for_the_whole_job_over_its_runs(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    spotdown.connect_error = True
    owner = await on_owner(harness)
    row = await two_runs(harness, owner, monkeypatch)
    assert (row.state, row.error_category) == ("completed", None)
    assert [s.path for s in spotdown.seen] == ["/v2/analyses/lookup"]  # the first run only
    assert [r.analyser for r in rows(harness, owner)] == ["local", "local"]
    assert [b.outage for b in budget(harness, owner)] == [True]


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
    assert row.state == "completed"
    assert len(sent(spotdown, "/v2/analyses/lookup")) == 1  # nothing in the second run
    assert [r.analyser for r in rows(harness, owner)] == ["spotdown", "local"]
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


async def test_three_provider_errors_are_an_outage_for_the_job(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    four, five = "trk-four-04", "trk-five-05"
    harness.gateway.add_audio(four, kick_wav_bytes(32.0))
    harness.gateway.add_audio(five, kick_wav_bytes(33.0))
    spotdown.end = "provider_error"
    owner = await on_owner(harness)
    await harness.run(owner, [ONE, TWO, four, five])
    # Three tracks of three ended provider_error (each retried once); the fourth calls nothing.
    assert len(sent(spotdown, "/v2/uploads")) == 6
    assert [b.outage for b in budget(harness, owner)] == [True]
    assert {r.analyser for r in rows(harness, owner)} == {"local"}


# --- an error of any type in any spotdown step falls back (#1302 review B3) ---------------------


def _raising(exc: BaseException, generator: bool = False) -> Any:
    async def coroutine(*_args: object, **_kwargs: object) -> None:
        raise exc

    async def chunks(*_args: object, **_kwargs: object) -> Any:
        raise exc
        yield b""  # pragma: no cover - makes this an async generator

    return chunks if generator else coroutine


@pytest.mark.parametrize(
    ("target", "name", "exc"),
    [
        (SpotdownClient, "lookup_content", OSError("socket")),
        (SpotdownClient, "upload", ValueError("bad")),
        (track_spotdown, "chunks", PermissionError("audio")),
        (SpotdownClient, "start_analysis", KeyError("x")),
        (SpotdownClient, "poll", TypeError("x")),
        (SpotdownClient, "get_analysis", httpx.ReadError("x")),
        (track_spotdown, "studio_map", AttributeError("x")),
        (track_spotdown, "valid_track_map", ZeroDivisionError()),
        (track_spotdown.budget, "open", LookupError("x")),
    ],
)
async def test_an_error_of_any_type_in_a_spotdown_step_falls_back(
    harness: Harness,
    spotdown: FakeSpotdown,
    monkeypatch: pytest.MonkeyPatch,
    work: Path,
    target: Any,
    name: str,
    exc: BaseException,
) -> None:
    off_result, off_maps = await baseline(harness, [ONE])
    if name in ("studio_map", "valid_track_map"):
        monkeypatch.setattr(target, name, lambda *_a, **_k: (_ for _ in ()).throw(exc))
    else:
        monkeypatch.setattr(target, name, _raising(exc, generator=name == "chunks"))
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("completed", None)
    assert last_result(harness) == off_result
    assert [(r.reference, bytes(r.sha256), r.map) for r in rows(harness, owner)] == off_maps
    assert not any(work.iterdir())  # noqa: ASYNC240


# --- shadow mode and the cache's own owner filter (#1302 review, non-blocking 4 and 5) ----------


async def test_no_shadow_for_a_track_that_fell_back_while_spotdown_is_the_source(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    shadow_config.configure(shadow_config.ShadowConfig(True, frozenset({"upload"}), KEY))
    spotdown.end = "processing_failed:decode_error"
    owner = await on_owner(harness)
    await harness.run(owner, [ONE])
    assert [r.analyser for r in rows(harness, owner)] == ["local"]
    assert harness.shadows(owner) == []
    assert len(sent(spotdown, "/v2/uploads")) == 1  # spotdown's own try, no shadow relay


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


async def test_a_failing_outage_record_still_falls_back(
    harness: Harness, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(track_spotdown.budget, "mark_outage", _raising(OSError("db")))
    spotdown.connect_error = True
    off_result, _maps = await baseline(harness, [ONE])
    owner = await on_owner(harness)
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("completed", None)
    assert last_result(harness) == off_result


async def test_switched_on_again_the_spotdown_row_is_the_hit_before_the_local_one(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    owner = await on_owner(harness)
    await harness.run(owner, [ONE])
    studio_tracks_config.configure(None)
    await harness.run(owner, [ONE])  # a local row beside the spotdown one
    turn_on(owner)
    seen = len(spotdown.seen)
    await harness.run(owner, [ONE])
    [(_ref, _sha, music_map, from_cache)] = last_result(harness)
    spotdown_row = next(r for r in rows(harness, owner) if r.analyser == "spotdown")
    assert (music_map, from_cache) == (spotdown_row.map, True)
    assert len(spotdown.seen) == seen
