"""SR C4 (ADR 0036 section 4.1): the studio's per-track analysis step
(``classbeat.design.track_analysis.analyse_tracks``) as a job on the fakes: the fake gateway
(``gatewayfake``, in process over an httpx transport) and the real sandboxed child on generated
audio. Never the network: the autouse netguard refuses any other connection.

- a fresh analysis downloads each track once, stores its map with the SHA-256 of the bytes and
  leaves no audio; a cache hit makes no download (and needs no analysis slot);
- the audio directory is gone after a success and after every failure;
- the size cap, a missing file, the sandbox, the daily limit and the gateway's statuses end in
  named categories, with nothing stored.
"""

import hashlib
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from mowl import track
from pydantic import BaseModel, ConfigDict, SecretStr

from authtools import Env, running_env
from classbeat.connections.swedebeat_account import open_bearer
from classbeat.design import mixanalysis, mixfetch, track_analysis, tracklimit
from classbeat.design.track_analysis import analyse_tracks
from classbeat.design.track_cache import TrackMap
from classbeat.jobs import Complete, JobKind, JobRun, KindRegistry
from classbeat.swedebeat_gateway.track_policy import DOWNLOAD_MAX
from classbeat.swedebeat_match import gateway_match
from ctx import make_context
from gatewaycore import FakeGateway
from gatewayfake import CLIENT_KEY, calls, store_bearer
from jobtools import new_job
from mediafake import kick_wav_bytes
from optools import KIND as JOB_KIND
from optools import OpsEnv

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default")
ONE, TWO = "trk-one-01", "trk-two-02"
AUDIO = kick_wav_bytes(30.0)  # a 124 BPM kick track; WAV bytes served as audio/mpeg


def route(reference: str) -> str:
    return f"/tracks/{reference}/download"


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url) as running:
        yield running


@pytest.fixture(autouse=True)
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The audio work root of these tests, so a leftover is visible."""
    root = tmp_path / "audio-root"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(mixfetch, "work_root", lambda: root)
    return root


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    async def pause(_seconds: float) -> None:
        return None

    monkeypatch.setattr(gateway_match, "_pause", pause)


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
            await job_run.stage("tracks")
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
        ctx = make_context(owner)
        await store_bearer(self.ops.factory, self.ops.keyring, ctx, self.gateway)
        return owner

    async def run(self, owner: UUID, references: list[str], **columns: Any) -> Any:
        body = json.dumps({"references": references})
        job_id = new_job(self.ops.admin, owner, input=body, max_attempts=1, **columns)
        await self.ops.run_next()
        return self.ops.row(job_id)

    def maps(self, owner: UUID) -> list[Any]:
        return self.ops.sql(
            "SELECT * FROM swedebeat_track_maps WHERE owner_id = :o ORDER BY reference", o=owner
        ).all()

    def downloads(self, owner: UUID) -> int:
        statement = "SELECT count(*) FROM swedebeat_track_downloads WHERE owner_id = :o"
        return self.ops.sql(statement, o=owner).scalar_one()

    def fetched(self) -> list[str]:
        return [r.path for r in self.gateway.requests if r.path.startswith("/tracks/")]


@pytest.fixture
def harness(xenv: OpsEnv, gateway_api: FakeGateway) -> Harness:
    gateway_api.add_audio(ONE, AUDIO)
    gateway_api.add_audio(TWO, AUDIO)
    return Harness(xenv, gateway_api)


def leftovers(work: Path) -> list[str]:
    return [p.name for p in work.iterdir()]


# --- a fresh analysis, then the cache --------------------------------------------------------


async def test_each_track_is_downloaded_analysed_stored_and_its_audio_deleted(
    harness: Harness, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real, at_store = track_analysis.store, list[list[str]]()

    async def store(*args: Any, **kwargs: Any) -> None:
        at_store.append(leftovers(work))  # the audio is gone before the map is stored
        await real(*args, **kwargs)

    monkeypatch.setattr(track_analysis, "store", store)
    owner = await harness.user()
    row = await harness.run(owner, [ONE, TWO, ONE])
    assert at_store == [[], []]
    assert (row.state, row.error_category) == ("completed", None)
    assert row.analysis_slot is False  # freed with the last store
    (result,) = harness.results
    assert [t.reference for t in result] == [ONE, TWO]  # in order, once each
    assert [t.cached for t in result] == [False, False]
    for analysed in result:
        (song,) = analysed.music_map["songs"]
        assert song["bpm"] == pytest.approx(124.0, abs=0.5)
        assert analysed.music_map["track_version"] == track.TRACK_VERSION
        assert analysed.sha256 == hashlib.sha256(AUDIO).hexdigest()
    assert harness.fetched() == [route(ONE), route(TWO)]  # one download each
    assert leftovers(work) == []  # the audio and the child's cache are gone
    stored = harness.maps(owner)
    assert [(m.reference, m.analyser_version) for m in stored] == [
        (ONE, track.TRACK_VERSION),
        (TWO, track.TRACK_VERSION),
    ]
    assert bytes(stored[0].sha256) == hashlib.sha256(AUDIO).digest()
    assert stored[0].map == result[0].music_map
    assert harness.downloads(owner) == 2  # the daily slots of the two misses


async def test_a_cache_hit_makes_no_download_and_needs_no_analysis_slot(
    harness: Harness, work: Path
) -> None:
    owner = await harness.user()
    await harness.run(owner, [ONE])
    seen = list(harness.gateway.requests)
    row = await harness.run(owner, [ONE], analysis_slot=False)
    assert (row.state, row.error_category) == ("completed", None)
    assert [t.cached for t in harness.results[-1]] == [True]
    assert harness.results[-1][0].music_map == harness.results[0][0].music_map
    assert harness.gateway.requests == seen  # nothing at all was called
    assert harness.downloads(owner) == 1
    assert leftovers(work) == []
    # Only the miss is downloaded when one track is new.
    row = await harness.run(owner, [ONE, TWO])
    assert row.state == "completed"
    assert [t.cached for t in harness.results[-1]] == [True, False]
    assert harness.fetched() == [route(ONE), route(TWO)]


async def test_another_owners_cache_is_not_a_hit(harness: Harness) -> None:
    first, second = await harness.user(), await harness.user()
    await harness.run(first, [ONE])
    await harness.run(second, [ONE])
    assert [t.cached for t in harness.results[-1]] == [False]
    assert harness.fetched() == [route(ONE), route(ONE)]


async def test_an_older_analyser_version_is_analysed_again(harness: Harness) -> None:
    owner = await harness.user()
    harness.ops.sql(
        "INSERT INTO swedebeat_track_maps (owner_id, reference, analyser_version, sha256, map) "
        "VALUES (:o, :r, :v, :h, '{}'::jsonb)",
        o=owner, r=ONE, v=track.TRACK_VERSION + 1, h=bytes(32),
    )  # fmt: skip
    row = await harness.run(owner, [ONE])
    assert row.state == "completed"
    assert harness.fetched() == [route(ONE)]


async def test_a_miss_without_a_slot_is_requeued_before_any_slot_or_download(
    harness: Harness,
) -> None:
    owner = await harness.user()
    row = await harness.run(owner, [ONE], analysis_slot=False)
    assert (row.state, row.analysis_slot) == ("queued", True)
    assert (harness.fetched(), harness.downloads(owner), harness.maps(owner)) == ([], 0, [])


# --- failures: named, nothing stored, no audio left -----------------------------------------


@pytest.mark.parametrize(
    ("injection", "category"),
    [
        ({"kind": "status", "status": 413}, "track_too_large"),
        ({"kind": "declared", "size": DOWNLOAD_MAX + 1}, "track_too_large"),
        ({"kind": "status", "status": 404}, "track_unavailable"),
        ({"kind": "unknown_client"}, "app_misconfigured"),
        ({"kind": "status", "status": 403}, "app_misconfigured"),
        ({"kind": "rate_limited", "retry_after": "42"}, "rate_limited"),
        ({"kind": "status", "status": 503}, "upstream_unavailable"),
        ({"kind": "gzip"}, "invalid_response"),
        ({"kind": "redirect", "status": 302, "location": "/auth/login"}, "outbound_blocked"),
    ],
)
async def test_a_refused_download_is_a_named_category_with_no_audio_left(
    harness: Harness, work: Path, injection: dict[str, Any], category: str
) -> None:
    owner = await harness.user()
    harness.gateway.inject(path=route(TWO), **injection)
    row = await harness.run(owner, [ONE, TWO])
    assert (row.state, row.error_category) == ("failed", category)
    assert leftovers(work) == []
    assert [m.reference for m in harness.maps(owner)] == [ONE]  # the first is kept for a retry


async def test_a_reference_without_a_file_is_track_unavailable(
    harness: Harness, work: Path
) -> None:
    owner = await harness.user()
    row = await harness.run(owner, ["trk-no-file"])
    assert (row.state, row.error_category) == ("failed", "track_unavailable")
    assert (leftovers(work), harness.maps(owner)) == ([], [])


async def test_a_refused_bearer_is_confirmed_then_needs_reconnect(harness: Harness) -> None:
    owner = await harness.user()
    harness.gateway.inject("bearer_401", route(ONE))
    harness.gateway.inject("bearer_401", "/auth/session")
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("failed", "needs_reconnect")
    assert len(calls(harness.gateway, "/auth/session")) == 1
    status = harness.ops.sql(
        "SELECT status FROM connections WHERE owner_id = :o AND provider = 'swedebeat'", o=owner
    ).scalar_one()
    assert status == "needs_reconnect"


async def test_audio_that_does_not_decode_is_analysis_failed_and_deleted(
    harness: Harness, work: Path
) -> None:
    owner = await harness.user()
    harness.gateway.add_audio(ONE, b"\xff\xfb" + b"not audio at all" * 64)
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("failed", "analysis_failed")
    assert harness.fetched() == [route(ONE)]
    assert (leftovers(work), harness.maps(owner)) == ([], [])


async def test_an_answer_without_the_system_call_filter_is_refused_on_linux(
    harness: Harness, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parent refuses a Linux child that does not say the seccomp filter held (T109): a
    stand-in child that answers a good map without it, on a parent that counts as Linux."""
    good = {
        "duration": 30.0,
        "songs": [{"start": 0, "end": 30.0, "bpm": 124.0, "first_downbeat": 0.5}],
        "events": [],
        "sections": [{"start": 0.0, "end": 30.0, "energy": None}],
        "track_version": track.TRACK_VERSION,
    }
    answer = json.dumps({"map": good, "peak_rss": None,
                         "sandbox": {"syscalls": False, "files": False}})  # fmt: skip
    seen: list[list[str]] = []

    def argv(audio: Path, max_seconds: float, max_rss: int, mode: str = "mix") -> list[str]:
        seen.append([audio.name, mode])
        return [sys.executable, "-I", "-c", f"print({answer!r})"]

    monkeypatch.setattr(mixanalysis, "child_argv", argv)
    monkeypatch.setattr(mixanalysis, "LINUX", True)
    owner = await harness.user()
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("failed", "analysis_failed")
    assert seen == [["track.mp3", "track"]]
    assert (leftovers(work), harness.maps(owner)) == ([], [])
    # The same answer with the filter held is stored: the refusal is the filter's.
    answer = answer.replace('"syscalls": false', '"syscalls": true')
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("completed", None)


async def test_the_daily_limit_refuses_before_any_download(harness: Harness) -> None:
    owner = await harness.user()
    harness.ops.sql(
        "INSERT INTO swedebeat_track_downloads (owner_id) "
        "SELECT :o FROM generate_series(1, :n)",
        o=owner, n=tracklimit.DOWNLOADS_PER_DAY - 1,
    )  # fmt: skip
    row = await harness.run(owner, [ONE, TWO])  # two misses, one slot left
    assert (row.state, row.error_category) == ("failed", "track_limit")
    assert harness.fetched() == []
    assert harness.downloads(owner) == tracklimit.DOWNLOADS_PER_DAY - 1  # nothing taken
    row = await harness.run(owner, [ONE])  # one fits
    assert row.state == "completed"


@pytest.mark.parametrize("references", [["bad/ref"], [""], [f"t{n}" for n in range(41)]])
async def test_bad_references_are_invalid_input_before_anything(
    harness: Harness, references: list[str]
) -> None:
    owner = await harness.user()
    row = await harness.run(owner, references)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert harness.gateway.requests == []


async def test_a_disconnect_before_the_store_stores_nothing(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Disconnect while the track is analysed: the store holds the connection and finds it
    gone, so the forgotten rows are not written back."""
    from classbeat import connections

    owner = await harness.user()
    real = mixanalysis.analyse_audio

    async def disconnect_first(*args: Any, **kwargs: Any) -> Any:
        result = await real(*args, **kwargs)
        assert await connections.disconnect(harness.ops.factory, make_context(owner), "swedebeat")
        return result

    monkeypatch.setattr(track_analysis, "analyse_audio", disconnect_first)
    row = await harness.run(owner, [ONE])
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert harness.maps(owner) == []


# --- SR C5: the C4 review's carry-overs ------------------------------------------------------


async def test_a_run_holds_the_slot_for_one_batch_then_is_requeued_for_the_rest(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Other users' analyses are not kept waiting behind a whole list: after ``SLOT_BATCH``
    downloads the slot is freed and the run is requeued; the next attempt takes the rest and
    finds the first batch in the cache."""
    monkeypatch.setattr(track_analysis, "SLOT_BATCH", 1)
    owner = await harness.user()
    job_id = new_job(
        harness.ops.admin, owner, input=json.dumps({"references": [ONE, TWO]}), max_attempts=1
    )
    await harness.ops.run_next()
    row = harness.ops.row(job_id)
    assert (row.state, row.analysis_slot) == ("queued", True)  # requeued, the attempt not counted
    assert harness.fetched() == [route(ONE)]
    assert [m.reference for m in harness.maps(owner)] == [ONE]
    await harness.ops.run_next()
    row = harness.ops.row(job_id)
    assert (row.state, row.error_category, row.analysis_slot) == ("completed", None, False)
    assert harness.fetched() == [route(ONE), route(TWO)]  # ONE was not downloaded again
    assert [t.cached for t in harness.results[-1]] == [True, False]
    assert harness.downloads(owner) == 2


async def test_a_retried_run_reserves_only_the_downloads_it_starts(harness: Harness) -> None:
    """A failed download counts once; the retry reserves for its own downloads, never the whole
    list again (one reservation per download, not per run)."""
    owner = await harness.user()
    harness.gateway.inject(path=route(ONE), kind="status", status=503)  # once
    row = await harness.run(owner, [ONE, TWO])
    assert (row.state, row.error_category) == ("failed", "upstream_unavailable")
    assert harness.downloads(owner) == 1  # the failed download of ONE, nothing for TWO
    row = await harness.run(owner, [ONE, TWO])
    assert row.state == "completed"
    assert harness.downloads(owner) == 3  # ONE twice, TWO once: the downloads started
