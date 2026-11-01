"""A3 of ADR 0016: the music step for a Swedebeat mix (``classbeat.design.swedebeat_music``) as a
job on the fakes: the fake MOWL (``mowlfake``, over loopback HTTP), the fake media endpoint
(``mediafake``, in-process, an injected list) and the real limited child process on generated
audio. Never the network.

``# ruff: noqa`` below: the tests poll and read files from async code, as the other job tests do.
"""

import asyncio
import io
import json
import logging
import sys
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from mowl import music
from pydantic import BaseModel, ConfigDict

from authtools import Env, running_env
from classbeat.design import mixanalysis, mixfetch
from classbeat.design.swedebeat_music import MAPS_PER_USER, MixAnalysis, load_swedebeat_mix
from classbeat.egress import Allowlist, mowl_media_test_client
from classbeat.jobs import Complete, JobKind, JobRun, KindRegistry
from classbeat.log import QUIET_LOGGERS, configure_logging
from jobtools import new_job
from mediafake import FakeMedia
from mowlfake import FakeMowl
from optools import KIND as JOB_KIND
from optools import TOKEN_A, TOKEN_B, OpsEnv

pytestmark = pytest.mark.usefixtures("mowl_api")
SAME_ID = "shared-mix-0001"
TABLES = (
    "jobs",
    "job_events",
    "external_operations",
    "mowl_playlist_links",
    "swedebeat_mix_maps",
)


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url, swedebeat_enabled=True) as running:
        yield running


@pytest.fixture(autouse=True)
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The audio directory of these tests, so leftovers are visible."""
    root = tmp_path / "audio-root"
    root.mkdir()
    cache = mixanalysis.numba_cache_dir()  # the real one, shared: compiling takes a while
    monkeypatch.setattr(mixfetch, "work_root", lambda: root)
    monkeypatch.setattr(mixanalysis, "numba_cache_dir", lambda: cache)
    return root


@pytest.fixture
def operator_log() -> Iterator[io.StringIO]:
    root = logging.getLogger()
    names = (*QUIET_LOGGERS, "sqlalchemy.engine", "sqlalchemy.pool")
    saved = (list(root.handlers), root.level, {n: logging.getLogger(n).level for n in names})
    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    try:
        yield stream
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        for name, level in saved[2].items():
            logging.getLogger(name).setLevel(level)


class MixInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mix_id: str
    connection_id: UUID


class Harness:
    """Runs jobs that load one mix's analysis; ``injected`` is the fake media endpoint's client
    (the list of its host), ``None`` the production client with its empty list."""

    def __init__(self, ops: OpsEnv, media: FakeMedia) -> None:
        self.ops = ops
        self.media = media
        self.injected = True
        self.total_seconds: float | None = None
        self.results: list[MixAnalysis] = []
        ops.registry = self.registry  # type: ignore[method-assign]

    def client(self) -> httpx.AsyncClient:
        kwargs = {} if self.total_seconds is None else {"total_seconds": self.total_seconds}
        return mowl_media_test_client(Allowlist(frozenset({FakeMedia.HOST})), self.media, **kwargs)

    def registry(self) -> KindRegistry:
        async def run(job_run: JobRun) -> Complete:
            await job_run.stage("music")
            given = job_run.input
            assert isinstance(given, MixInput)
            extra: dict[str, Any] = {"client_factory": self.client} if self.injected else {}
            self.results.append(
                await load_swedebeat_mix(
                    job_run,
                    self.ops.keyring,
                    given.mix_id,
                    connection_id=given.connection_id,
                    index_poll_seconds=0.01,
                    **extra,
                )
            )
            return Complete()

        registry = KindRegistry()
        registry.register(JobKind(JOB_KIND, MixInput, ("music",), run))
        return registry

    def job(self, owner: UUID, connection: UUID, mix_id: str) -> UUID:
        body = {"mix_id": mix_id, "connection_id": str(connection)}
        return new_job(self.ops.admin, owner, input=json.dumps(body), max_attempts=1)

    async def run(self, owner: UUID, connection: UUID, mix_id: str) -> Any:
        job_id = self.job(owner, connection, mix_id)
        await self.ops.run_next()
        return self.ops.row(job_id)

    def maps(self, owner: UUID) -> list[Any]:
        return self.ops.sql(
            "SELECT * FROM swedebeat_mix_maps WHERE owner_id = :o ORDER BY created_at, mix_id",
            o=owner,
        ).all()

    def dump(self) -> str:
        parts = [
            str(self.ops.sql(f"SELECT row_to_json(t)::text FROM {table} t").all())  # noqa: S608
            for table in TABLES
        ]
        return "\n".join(parts)


@pytest.fixture
def media() -> FakeMedia:
    return FakeMedia()


@pytest.fixture
def harness(xenv: OpsEnv, mowl_api: FakeMowl) -> Harness:
    return Harness(xenv, mowl_api.media)


async def account(h: Harness, token: str = TOKEN_A) -> tuple[UUID, UUID]:
    owner = await h.ops.user()
    return owner, await h.ops.connect(owner, token)


def leftovers(work: Path) -> list[str]:
    return [p.name for p in work.iterdir()]


def add_beats(mowl_api: FakeMowl, name: str = "Sunday Climb", **kwargs: Any) -> str:
    kwargs = {"token": TOKEN_A, "listed_seconds": 60.0, "link": "beats", **kwargs}
    return mowl_api.add_mix(name, **kwargs)


# --- the fetch and the analysis, end to end -----------------------------------------------------


async def test_the_fake_mix_is_fetched_analysed_cached_and_its_audio_deleted(
    harness: Harness,
    mowl_api: FakeMowl,
    work: Path,
    operator_log: io.StringIO,
) -> None:
    owner, connection = await account(harness)
    mix = add_beats(mowl_api)
    row = await harness.run(owner, connection, mix)
    assert (row.state, row.error_category) == ("completed", None)
    (result,) = harness.results
    assert result.cached is False
    (song,) = result.music_map["songs"]
    assert song["bpm"] == pytest.approx(124.0, abs=0.5)
    assert result.music_map["version"] == music.MAP_VERSION
    # One GET to the media host; none of our headers; the audio is gone.
    assert [m for m, _h, _p in harness.media.requests] == ["GET"]
    assert set(harness.media.headers[0]) <= {"host"}
    assert leftovers(work) == []
    # The cache row.
    (stored,) = harness.maps(owner)
    assert (stored.mix_id, stored.mowl_connection_id) == (mix, connection)
    assert stored.playlist_id == result.playlist_id
    assert stored.name == "Sunday Climb"
    assert stored.duration == pytest.approx(60.0)
    assert stored.listed_duration == pytest.approx(60.0)
    assert stored.map == result.music_map
    # Peak memory of the child, in the operator log's counts: under the 1.5 GiB limit.
    peaks = [
        json.loads(line).get("peak_rss_mb")
        for line in operator_log.getvalue().splitlines()
        if "swedebeat mix analysed" in line
    ]
    assert len(peaks) == 1
    assert peaks[0] is not None
    assert 0 < peaks[0] < 1536


async def test_a_cache_hit_makes_no_request_at_all(
    harness: Harness, mowl_api: FakeMowl, work: Path
) -> None:
    owner, connection = await account(harness)
    mix = add_beats(mowl_api)
    assert (await harness.run(owner, connection, mix)).state == "completed"
    seen = (list(mowl_api.requests), list(harness.media.requests), mowl_api.link_requests)
    second = await harness.run(owner, connection, mix)
    assert second.state == "completed"
    assert harness.results[1].cached is True
    assert harness.results[1].music_map == harness.results[0].music_map
    assert harness.results[1].fingerprint == harness.results[0].fingerprint
    assert (list(mowl_api.requests), list(harness.media.requests), mowl_api.link_requests) == seen
    assert leftovers(work) == []


async def test_a_cached_map_made_for_another_connection_playlist_age_or_version_is_not_reused(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, connection = await account(harness)
    mix = add_beats(mowl_api)
    assert (await harness.run(owner, connection, mix)).state == "completed"

    async def refetched(change: str, **params: Any) -> None:
        before = len(harness.media.requests)
        harness.ops.sql(change, o=owner, **params)
        assert (await harness.run(owner, connection, mix)).state == "completed"
        assert len(harness.media.requests) == before + 1, change
        assert harness.results[-1].cached is False

    # A map of another MOWL connection (no foreign key holds it, the step compares it).
    await refetched(
        "UPDATE swedebeat_mix_maps SET mowl_connection_id = :other WHERE owner_id = :o",
        other=uuid.uuid4(),
    )
    # Older than 30 days.
    await refetched(
        "UPDATE swedebeat_mix_maps SET created_at = now() - interval '31 days' WHERE owner_id = :o"
    )
    # Another MOWL playlist than the stored link's.
    await refetched(
        "UPDATE swedebeat_mix_maps SET playlist_id = playlist_id + 1 WHERE owner_id = :o"
    )
    # An older map format.
    await refetched(
        "UPDATE swedebeat_mix_maps SET map = jsonb_set(map, '{version}', '1') WHERE owner_id = :o"
    )
    assert len(harness.maps(owner)) == 1  # replaced in place, never a second row


async def test_each_user_has_their_own_map_of_the_same_mix_id(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    alice, alice_connection = await account(harness, TOKEN_A)
    bob, bob_connection = await account(harness, TOKEN_B)
    add_beats(mowl_api, "Alice's", token=TOKEN_A, mix_id=SAME_ID)
    add_beats(mowl_api, "Bob's", token=TOKEN_B, mix_id=SAME_ID, bpm=100.0)
    assert (await harness.run(alice, alice_connection, SAME_ID)).state == "completed"
    before = len(harness.media.requests)
    assert (await harness.run(bob, bob_connection, SAME_ID)).state == "completed"
    assert len(harness.media.requests) == before + 1  # Alice's cache served Bob nothing
    assert [r.cached for r in harness.results] == [False, False]
    (a,), (b,) = harness.maps(alice), harness.maps(bob)
    assert (a.name, b.name) == ("Alice's", "Bob's")
    assert a.playlist_id != b.playlist_id
    assert a.owner_id != b.owner_id


async def test_the_store_keeps_the_newest_twenty_and_drops_what_is_past_thirty_days(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, connection = await account(harness)
    other, _ = await account(harness, TOKEN_B)
    rows = MAPS_PER_USER + 2
    for number in range(rows):
        for who, age in ((owner, rows - number), (other, 0)):
            harness.ops.sql(
                "INSERT INTO swedebeat_mix_maps (owner_id, mix_id, mowl_connection_id, "
                "playlist_id, name, duration, listed_duration, map, created_at) "
                "VALUES (:o, :m, :c, 5, 'x', 60, 60, CAST(:map AS jsonb), "
                "now() - make_interval(hours => :age))",
                o=who,
                m=f"old-{number:02d}",
                c=connection,
                map=json.dumps({"version": music.MAP_VERSION}),
                age=age,
            )
    harness.ops.sql(
        "UPDATE swedebeat_mix_maps SET created_at = now() - interval '40 days' "
        "WHERE owner_id = :o AND mix_id = 'old-21'",
        o=owner,
    )
    mix = add_beats(mowl_api)
    assert (await harness.run(owner, connection, mix)).state == "completed"
    ids = [r.mix_id for r in harness.maps(owner)]
    assert len(ids) == MAPS_PER_USER
    assert mix in ids
    assert "old-21" not in ids  # past 30 days
    assert "old-00" not in ids and "old-01" not in ids  # the oldest, over the twenty
    assert len(harness.maps(other)) == rows  # another user's rows are never touched


# --- every refusal ends cleanly and secretly ---------------------------------------------------


async def test_an_empty_production_list_refuses_the_media_host_and_makes_no_media_request(
    harness: Harness, mowl_api: FakeMowl, work: Path, operator_log: io.StringIO
) -> None:
    harness.injected = False  # the production client: MOWL_MEDIA_HOSTS is empty
    owner, connection = await account(harness)
    mix = add_beats(mowl_api)
    row = await harness.run(owner, connection, mix)
    assert (row.state, row.error_category) == ("failed", "media_host_not_allowed")
    assert harness.media.requests == []
    assert mowl_api.link_requests == 1  # MOWL was asked for the link, the media host never
    assert harness.maps(owner) == []
    assert leftovers(work) == []
    text = operator_log.getvalue()
    assert FakeMedia.HOST in text  # the refused host name, for the owner's live check
    for signature in harness.media.signatures:
        assert signature not in text
        assert signature not in harness.dump()
    assert "sig=" not in text


async def test_a_mix_in_the_cache_still_works_with_the_empty_production_list(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, connection = await account(harness)
    mix = add_beats(mowl_api)
    assert (await harness.run(owner, connection, mix)).state == "completed"
    harness.injected = False
    again = await harness.run(owner, connection, mix)
    assert again.state == "completed"
    assert harness.results[-1].cached is True


@pytest.mark.parametrize(
    ("options", "category"),
    [
        ({"link": "status", "code": 403}, "media_refused"),
        ({"link": "status", "code": 410}, "media_refused"),
        ({"link": "status", "code": 500}, "upstream_unavailable"),
        ({"link": "redirect"}, "media_refused"),
        ({"link": "redirect_away"}, "media_host_not_allowed"),
        ({"link": "gzip"}, "media_refused"),
        ({"link": "oversize"}, "media_refused"),
        ({"link": "undecodable"}, "analysis_failed"),
        ({"link": "beats", "seconds": 400.0}, "analysis_failed"),  # 400 s of a listed 60 s + 5 min
    ],
)
async def test_every_media_refusal_and_bad_file_ends_the_job_with_one_clean_category(
    harness: Harness,
    mowl_api: FakeMowl,
    work: Path,
    operator_log: io.StringIO,
    options: dict[str, Any],
    category: str,
) -> None:
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A, listed_seconds=60.0, audio_seconds=60.0, **options)
    row = await harness.run(owner, connection, mix)
    assert (row.state, row.error_category) == ("failed", category)
    assert harness.maps(owner) == []
    assert leftovers(work) == []
    secrets = [*harness.media.signatures, FakeMedia.HOST + "/media"]
    everything = harness.dump() + operator_log.getvalue()
    for secret in secrets:
        assert secret not in everything
    assert "mix.mp3" not in everything


async def test_a_slow_body_ends_at_the_clients_deadline_and_is_retried_later(
    harness: Harness, mowl_api: FakeMowl, work: Path
) -> None:
    harness.total_seconds = 0.3
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Slow", token=TOKEN_A, link="slow", delay=0.05, chunks=1000)
    row = await harness.run(owner, connection, mix)
    assert (row.state, row.error_category) == ("failed", "upstream_unavailable")
    assert leftovers(work) == []


@pytest.mark.parametrize(
    ("switch", "category"),
    [
        ({"status": {"swedebeat_links": 401}}, "needs_reconnect"),
        ({"status": {"swedebeat_links": 429}}, "rate_limited"),
        ({"status": {"swedebeat_links": 503}}, "upstream_unavailable"),
        ({"links_answer": "empty"}, "upstream_unavailable"),
        ({"links_answer": "garbled"}, "upstream_unavailable"),
    ],
)
async def test_a_failing_or_empty_temporary_link_read_ends_cleanly_with_no_media_request(
    harness: Harness,
    mowl_api: FakeMowl,
    work: Path,
    switch: dict[str, Any],
    category: str,
) -> None:
    owner, connection = await account(harness)
    mix = add_beats(mowl_api)
    for key, value in switch.items():
        setattr(mowl_api, key, value)
    row = await harness.run(owner, connection, mix)
    assert (row.state, row.error_category) == ("failed", category)
    assert harness.media.requests == []
    assert harness.maps(owner) == []
    assert leftovers(work) == []


async def test_a_mix_outside_the_callers_own_listing_is_neither_attached_nor_fetched(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, connection = await account(harness, TOKEN_A)
    add_beats(mowl_api, "Mine", token=TOKEN_A)
    theirs = add_beats(mowl_api, "Theirs", token=TOKEN_B)
    for wanted in (theirs, "made-up-id"):
        row = await harness.run(owner, connection, wanted)
        assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert mowl_api.attaches == []
    assert mowl_api.link_requests == 0
    assert harness.media.requests == []


async def test_a_job_naming_another_connection_does_nothing(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner, _ = await account(harness)
    mix = add_beats(mowl_api)
    row = await harness.run(owner, uuid.uuid4(), mix)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert mowl_api.attaches == []
    assert harness.media.requests == []


# --- cancel and crash ----------------------------------------------------------------------------


async def until(check: Callable[[], bool], seconds: float = 30.0) -> None:
    end = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < end, "never happened"
        await asyncio.sleep(0.05)


async def test_a_cancel_during_the_download_stops_it_and_deletes_the_audio(
    harness: Harness, mowl_api: FakeMowl, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mixfetch, "CHECKPOINT_SECONDS", 0.05)
    owner, connection = await account(harness)
    mix = mowl_api.add_mix("Slow", token=TOKEN_A, link="slow", delay=0.02, chunks=2000, chunk=1000)
    job_id = harness.job(owner, connection, mix)
    task = asyncio.create_task(harness.ops.run_next())
    await until(lambda: any(work.glob("mix-*/mix.audio")))  # the file is being written
    harness.ops.sql("UPDATE jobs SET cancel_requested_at = now() WHERE id = :j", j=job_id)
    await asyncio.wait_for(task, 30)
    row = harness.ops.row(job_id)
    assert row.state == "cancelled"
    assert leftovers(work) == []
    assert harness.maps(owner) == []


async def test_a_cancel_during_the_analysis_kills_the_child_and_deletes_the_audio(
    harness: Harness, mowl_api: FakeMowl, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mixanalysis, "CHECKPOINT_SECONDS", 0.2)
    sleeper = [sys.executable, "-c", "import time; time.sleep(120)"]
    monkeypatch.setattr(mixanalysis, "child_argv", lambda _a, _s, _r: sleeper)
    owner, connection = await account(harness)
    mix = add_beats(mowl_api)
    job_id = harness.job(owner, connection, mix)
    task = asyncio.create_task(harness.ops.run_next())
    await until(lambda: any(work.glob("mix-*/mix.audio")) and len(harness.media.requests) == 1)
    await asyncio.sleep(0.5)  # the child is running
    harness.ops.sql("UPDATE jobs SET cancel_requested_at = now() WHERE id = :j", j=job_id)
    await asyncio.wait_for(task, 30)
    assert harness.ops.row(job_id).state == "cancelled"
    assert leftovers(work) == []
    assert harness.maps(owner) == []


async def test_a_crash_of_the_child_ends_the_job_analysis_failed_and_the_worker_lives_on(
    harness: Harness, mowl_api: FakeMowl, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crash = [sys.executable, "-c", "import os; os._exit(9)"]
    owner, connection = await account(harness)
    mix = add_beats(mowl_api)
    with monkeypatch.context() as patched:
        patched.setattr(mixanalysis, "child_argv", lambda _a, _s, _r: crash)
        row = await harness.run(owner, connection, mix)
    assert (row.state, row.error_category) == ("failed", "analysis_failed")
    assert leftovers(work) == []
    # The next job, in the same process, goes through.
    second = await harness.run(owner, connection, mix)
    assert second.state == "completed"


async def test_an_error_in_the_step_itself_still_deletes_the_audio(
    harness: Harness, mowl_api: FakeMowl, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explode(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr("classbeat.design.swedebeat_music.analyse_audio", explode)
    owner, connection = await account(harness)
    mix = add_beats(mowl_api)
    row = await harness.run(owner, connection, mix)
    assert (row.state, row.error_category) == ("failed", "internal")
    assert leftovers(work) == []


# --- secrecy -------------------------------------------------------------------------------------


async def test_the_link_and_its_signature_are_in_no_log_row_or_error(
    harness: Harness, mowl_api: FakeMowl, operator_log: io.StringIO
) -> None:
    owner, connection = await account(harness)
    good = add_beats(mowl_api, "Good")
    bad = mowl_api.add_mix("Bad", token=TOKEN_A, link="status", code=403)
    assert (await harness.run(owner, connection, good)).state == "completed"
    assert (await harness.run(owner, connection, bad)).state == "failed"
    harness.injected = False
    other = add_beats(mowl_api, "Other")
    assert (await harness.run(owner, connection, other)).state == "failed"
    everything = harness.dump() + operator_log.getvalue()
    assert len(harness.media.signatures) >= 3
    for signature in harness.media.signatures:
        assert signature not in everything
    assert TOKEN_A not in everything
    assert "mix.mp3" not in everything
