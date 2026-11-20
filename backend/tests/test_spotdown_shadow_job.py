"""SD-P4 part 3 (#1301, ADR 0040 section 9): shadow mode for studio tracks as a job on the fakes:
the fake gateway, the fake spotdown (``spotdownfake``, in process) and the real sandboxed child on
generated audio. Never the network: the autouse netguard refuses any other connection.

- off (the default), or on without spotdown installed: nothing is sent, nothing is written;
- on: the bytes of each downloaded track are looked up, then uploaded once, in the owner's own
  scope; the comparison and the provenance are recorded, path (a) ``exact``, with no reference,
  scope, client ref or title; the analysis step's result and stored maps are those of a run
  without shadow mode;
- every failure (spotdown unreachable, a per-track status, the budget, an error in the shadow
  code, the database refusing its write) is an outcome, and never changes the run;
- owners are isolated: rows by row security, analyses by scope.
"""

import hashlib
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import text

from authtools import Env, running_env
from classbeat.connections.swedebeat_account import open_bearer
from classbeat.db import user_session
from classbeat.design import mixfetch, track_shadow, track_shadow_rows
from classbeat.design.track_analysis import analyse_tracks
from classbeat.design.track_cache import TrackMap
from classbeat.jobs import Complete, JobKind, JobRun, KindRegistry
from classbeat.spotdown import shadow_config, shadow_policy
from classbeat.spotdown.scope_calc import cache_scope
from classbeat.swedebeat_match import gateway_match
from ctx import make_context
from gatewaycore import FakeGateway
from gatewayfake import CLIENT_KEY, store_bearer
from jobtools import new_job
from mediafake import kick_wav_bytes
from optools import KIND as JOB_KIND
from optools import OpsEnv
from spotdownfake import FakeSpotdown, spotdown

__all__ = ["spotdown"]  # the fixture

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default")
ONE, TWO = "trk-one-01", "trk-two-02"
AUDIO = kick_wav_bytes(30.0)
OTHER_AUDIO = kick_wav_bytes(31.0)
SHA = hashlib.sha256(AUDIO).hexdigest()
KEY = bytes(range(32))
ON = shadow_config.ShadowConfig(True, frozenset({"upload"}), KEY)


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url) as running:
        yield running


@pytest.fixture(autouse=True)
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "audio-root"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(mixfetch, "work_root", lambda: root)
    return root


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    async def pause(*_args: object) -> None:
        return None

    monkeypatch.setattr(gateway_match, "_pause", pause)
    monkeypatch.setattr(track_shadow, "_pause", pause)


@pytest.fixture
def shadow_on() -> Iterator[None]:
    shadow_config.configure(ON)
    try:
        yield
    finally:
        shadow_config.configure(None)


class TrackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    references: list[str]


class Harness:
    def __init__(self, ops: OpsEnv, gateway: FakeGateway) -> None:
        self.ops, self.gateway = ops, gateway
        self.results: list[list[TrackMap]] = []
        ops.registry = self.registry  # type: ignore[method-assign]

    def registry(self) -> KindRegistry:
        async def run(job_run: JobRun) -> Complete:
            given = job_run.input
            assert isinstance(given, TrackInput)
            bearer = await open_bearer(job_run.factory, self.ops.keyring, job_run.ctx)
            key = SecretStr(CLIENT_KEY)
            self.results.append(await analyse_tracks(job_run, bearer, key, given.references))
            return Complete()

        registry = KindRegistry()
        registry.register(JobKind(JOB_KIND, TrackInput, ("tracks",), run))
        return registry

    async def user(self) -> UUID:
        owner = await self.ops.user()
        await store_bearer(self.ops.factory, self.ops.keyring, make_context(owner), self.gateway)
        return owner

    async def run(self, owner: UUID, references: list[str]) -> Any:
        body = json.dumps({"references": references})
        job_id = new_job(self.ops.admin, owner, input=body, max_attempts=1)
        await self.ops.run_next()
        return self.ops.row(job_id)

    def shadows(self, owner: UUID) -> list[Any]:
        return self.ops.sql(
            "SELECT * FROM spotdown_shadow_tracks WHERE owner_id = :o ORDER BY started_at, id",
            o=owner,
        ).all()

    def maps(self, owner: UUID) -> list[tuple[str, bytes, Any]]:
        rows = self.ops.sql(
            "SELECT reference, sha256, map FROM swedebeat_track_maps WHERE owner_id = :o "
            "ORDER BY reference",
            o=owner,
        ).all()
        return [(r.reference, bytes(r.sha256), r.map) for r in rows]


@pytest.fixture
def harness(xenv: OpsEnv, gateway_api: FakeGateway) -> Harness:
    gateway_api.add_audio(ONE, AUDIO)
    gateway_api.add_audio(TWO, OTHER_AUDIO)
    return Harness(xenv, gateway_api)


def spotdown_paths(fake: FakeSpotdown) -> list[str]:
    return [f"{s.method} {s.path}" for s in fake.seen]


async def baseline(harness: Harness, references: list[str]) -> tuple[list[Any], list[Any]]:
    """The result and stored maps of a run without shadow mode (another owner, the same audio)."""
    owner = await harness.user()
    row = await harness.run(owner, references)
    assert (row.state, row.error_category) == ("completed", None)
    result = [(t.reference, t.sha256, t.music_map, t.cached) for t in harness.results[-1]]
    return result, [(r, s, m) for r, s, m in harness.maps(owner)]


def last_result(harness: Harness) -> list[Any]:
    return [(t.reference, t.sha256, t.music_map, t.cached) for t in harness.results[-1]]


# --- off --------------------------------------------------------------------------------------


async def test_off_by_default_nothing_is_sent_or_written(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    owner = await harness.user()
    row = await harness.run(owner, [ONE, TWO])
    assert (row.state, row.error_category) == ("completed", None)
    assert spotdown.seen == []
    assert harness.shadows(owner) == []


async def test_on_without_spotdown_installed_nothing_is_sent_or_written(
    harness: Harness, shadow_on: None
) -> None:
    owner = await harness.user()  # no ``spotdown`` fixture: CLASSBEAT_SPOTDOWN_URL empty
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("completed", None)
    assert harness.shadows(owner) == []


async def test_on_without_the_upload_source_nothing_is_sent(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    shadow_config.configure(shadow_config.ShadowConfig(True, frozenset({"youtube"}), KEY))
    try:
        owner = await harness.user()
        await harness.run(owner, [ONE])
    finally:
        shadow_config.configure(None)
    assert (spotdown.seen, harness.shadows(owner)) == ([], [])


# --- on ---------------------------------------------------------------------------------------


async def test_each_track_is_compared_and_the_step_is_unchanged(
    harness: Harness, spotdown: FakeSpotdown, shadow_on: None, work: Path
) -> None:
    shadow_config.configure(None)
    off_result, off_maps = await baseline(harness, [ONE, TWO])
    shadow_config.configure(ON)
    owner = await harness.user()
    row = await harness.run(owner, [ONE, TWO])
    assert (row.state, row.error_category) == ("completed", None)
    assert last_result(harness) == off_result  # the design gets exactly today's analysis
    assert harness.maps(owner) == off_maps
    assert not any(work.iterdir())  # no audio left  # noqa: ASYNC240
    scope = cache_scope(KEY, owner)
    uploads = [s for s in spotdown.seen if s.path == "/v2/uploads"]
    assert [hashlib.sha256(s.body).hexdigest() for s in uploads] == [
        SHA,
        hashlib.sha256(OTHER_AUDIO).hexdigest(),
    ]
    assert all(f"cache_scope={scope}" in s.query for s in uploads)
    lookups = [s for s in spotdown.seen if s.path == "/v2/analyses/lookup"]
    assert len(lookups) == 2  # a lookup before each upload
    assert all(f"cache_scope={scope}" in s.query for s in lookups)
    sent = b"".join(s.body for s in spotdown.seen if s.path != "/v2/uploads")
    assert ONE.encode() not in sent  # no reference leaves
    assert TWO.encode() not in sent
    shadows = harness.shadows(owner)
    assert [(s.outcome, s.detail) for s in shadows] == [("compared", None)] * 2
    for s in shadows:
        assert s.metrics["timing_class"] == "exact"  # path (a): the played bytes
        assert s.metrics["timing_reason"] == "played_recording"
        assert s.metrics["bpm_local"] == pytest.approx(124.0, abs=0.5)
        assert s.metrics["analyzer_version"] == "1.0.0"
        assert s.provenance["audio"]["sha256"] in {SHA, hashlib.sha256(OTHER_AUDIO).hexdigest()}
        assert s.finished_at is not None
        stored = json.dumps([s.metrics, s.provenance, s.detail])
        for secret in (ONE, TWO, scope, "shadow-", "cache_scope", "Bearer"):
            assert secret not in stored


async def test_bytes_analysed_before_in_the_scope_are_not_uploaded_again(
    harness: Harness, spotdown: FakeSpotdown, shadow_on: None
) -> None:
    owner = await harness.user()
    spotdown.add_analysis(cache_scope(KEY, owner), f"sha256:{SHA}")
    await harness.run(owner, [ONE])
    assert "PUT /v2/uploads" not in spotdown_paths(spotdown)
    assert [s.outcome for s in harness.shadows(owner)] == ["compared"]


async def test_another_owners_analysis_is_not_reused_and_their_rows_are_not_visible(
    harness: Harness, spotdown: FakeSpotdown, shadow_on: None
) -> None:
    first, second = await harness.user(), await harness.user()
    await harness.run(first, [ONE])
    before = len([s for s in spotdown.seen if s.path == "/v2/uploads"])
    await harness.run(second, [ONE])
    uploads = [s for s in spotdown.seen if s.path == "/v2/uploads"]
    assert len(uploads) == before + 1  # the second owner's scope holds nothing of the first's
    assert f"cache_scope={cache_scope(KEY, second)}" in uploads[-1].query
    assert cache_scope(KEY, first) != cache_scope(KEY, second)
    async with user_session(harness.ops.factory, make_context(second)) as db:
        visible = (await db.execute(text("SELECT owner_id FROM spotdown_shadow_tracks"))).all()
    assert [r.owner_id for r in visible] == [second]  # row security: only their own


# --- failures are outcomes ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setup", "outcome", "detail"),
    [
        ({"connect_error": True}, "unreachable", "connection"),
        ({"end": "processing_failed:decode_error"}, "track_failure", "processing_failed"),
        ({"end": "provider_error"}, "track_failure", "provider_error"),
    ],
)
async def test_a_spotdown_failure_is_recorded_and_changes_nothing(
    harness: Harness,
    spotdown: FakeSpotdown,
    shadow_on: None,
    setup: dict[str, Any],
    outcome: str,
    detail: str,
) -> None:
    shadow_config.configure(None)
    off_result, off_maps = await baseline(harness, [ONE])
    shadow_config.configure(ON)
    for name, value in setup.items():
        setattr(spotdown, name, value)
    owner = await harness.user()
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("completed", None)
    assert last_result(harness) == off_result
    assert harness.maps(owner) == off_maps
    assert [(s.outcome, s.detail) for s in harness.shadows(owner)] == [(outcome, detail)]


async def test_the_wait_budget_ends_in_timed_out(
    harness: Harness, spotdown: FakeSpotdown, shadow_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shadow_policy, "WAIT_SECONDS", 0.0)
    owner = await harness.user()
    row = await harness.run(owner, [ONE])
    assert row.state == "completed"
    assert [s.outcome for s in harness.shadows(owner)] == ["timed_out"]
    assert not any(s.path.startswith("/v2/jobs/") for s in spotdown.seen)


async def test_an_error_in_the_shadow_code_is_swallowed_as_internal(
    harness: Harness, spotdown: FakeSpotdown, shadow_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object) -> None:
        raise RuntimeError("a bug in the comparison")

    monkeypatch.setattr(track_shadow, "evaluate", broken)
    owner = await harness.user()
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("completed", None)
    assert [s.outcome for s in harness.shadows(owner)] == ["internal"]
    assert len(harness.maps(owner)) == 1


@pytest.mark.parametrize("step", ["open_row", "close_rows", "first_started"])
async def test_a_refused_shadow_write_never_fails_the_run(
    harness: Harness,
    spotdown: FakeSpotdown,
    shadow_on: None,
    monkeypatch: pytest.MonkeyPatch,
    step: str,
) -> None:
    async def refused(*_args: object) -> None:
        raise RuntimeError("the database refused")

    monkeypatch.setattr(track_shadow_rows, step, refused)
    owner = await harness.user()
    row = await harness.run(owner, [ONE, TWO])
    assert (row.state, row.error_category) == ("completed", None)
    assert [m[0] for m in harness.maps(owner)] == [ONE, TWO]


async def test_a_swedebeat_disconnect_forgets_the_shadow_rows(
    harness: Harness, spotdown: FakeSpotdown, shadow_on: None
) -> None:
    from classbeat.connections.forget import forget_swedebeat_account

    owner, other = await harness.user(), await harness.user()
    await harness.run(owner, [ONE])
    await harness.run(other, [ONE])
    async with user_session(harness.ops.factory, make_context(owner)) as db:
        await forget_swedebeat_account(db, make_context(owner))
    assert harness.shadows(owner) == []
    assert len(harness.shadows(other)) == 1
