"""A4 of ADR 0016: ``design_draft`` (and ``revise_draft``) on the user's Swedebeat mix, then Save to
MOWL, through the real jobs API and worker side, on the fakes only: the fake MOWL (``mowlfake``,
over loopback HTTP), the fake media endpoint (``mediafake``, an injected list), the real limited
analysis child on generated audio, ``FakeProvider`` for the model, and for Save the program fake of
``savetools`` behind the worker's allowlist opener. Never the network.

Covered: a class designed and saved on a fake mix; what the model is sent holds no mix text or id;
the revision's music provenance and fingerprint; ``admit``'s own-listing check, flag and MOWL
connection; a queued job whose flag went off ends ``feature_off`` calling nothing; the daily limit
of new analyses (admit and worker; a cache hit costs none); a revise on the cached map.
"""

import asyncio
import json
import urllib.request
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from mowl import api as mowl_lib

from agentfake import CONFIG, FactoryModels, from_dict
from authtools import Env, running_env
from classbeat import design, saving
from classbeat.agent.providers.fake import FakeProvider
from classbeat.design import mixadmit, mixanalysis, mixfetch, mixlimit
from classbeat.design import swedebeat_music as sbm
from classbeat.design.revise import KIND as REVISE_KIND
from classbeat.egress import (
    WORKER_ALLOWLIST,
    Allowlist,
    build_allowlist_opener,
    mowl_media_test_client,
)
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from designtools import BRIEF
from jobapi import Browser, new_key
from jobtools import new_job
from mediafake import FakeMedia
from mowlfake import FakeMowl
from optools import TOKEN_A, TOKEN_B, OpsEnv
from savetools import FakeMowlPrograms

pytestmark = pytest.mark.usefixtures("jenv", "mowl_api")

MIX_NAME = "Planted Mix Name q9z"  # searched for in everything the model is sent
MIX_ID = "planted-mix-id-77"
SECONDS = 150.0


def short_draft() -> dict[str, Any]:
    """A class that fits the 150 s fake mix."""
    seated = {"standing": False}
    return {
        "name": "Invented mix ride",
        "segments": [
            {"name": "Warm-up", "kind": "warmup",
             "intervals": [{"sec": 45, "zone": 2, "rpm": "beat", **seated}]},
            {"name": "Push", "kind": "main",
             "intervals": [{"sec": 45, "zone": 4, "rpm": [80, 95], **seated}]},
            {"name": "Cool down", "kind": "cooldown",
             "intervals": [{"sec": 60, "zone": 1, "rpm": [70, 85], **seated}]},
        ],
    }  # fmt: skip


def script() -> dict[str, Any]:
    draft = {"draft": {"$draft": "good"}}
    return {
        "drafts": {"good": short_draft()},
        "turns": [
            {
                "calls": [
                    {"name": "get_music", "input": {}},
                    {"name": "get_music", "input": {"track": 0}},
                    {"name": "preview_draft", "input": draft},
                ]
            },
            {"calls": [{"name": "submit_draft", "input": draft}]},
        ],
    }


def revise_script() -> dict[str, Any]:
    changed = short_draft()
    changed["segments"][1]["intervals"][0]["zone"] = 5
    return {
        "drafts": {"good": changed},
        "turns": [
            {
                "calls": [
                    {"name": "submit_draft", "input": {"draft": {"$draft": "good"}, "note": "x"}}
                ]
            }
        ],
    }


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url, swedebeat_enabled=True) as running:
        yield running


@pytest.fixture(autouse=True)
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "audio-root"
    root.mkdir()
    cache = mixanalysis.numba_cache_dir()
    monkeypatch.setattr(mixfetch, "work_root", lambda: root)
    monkeypatch.setattr(mixanalysis, "numba_cache_dir", lambda: cache)
    return root


class MixEnv:
    """The worker side of the design kinds, with the fake media endpoint's client injected."""

    def __init__(self, ops: OpsEnv, media: FakeMedia, monkeypatch: pytest.MonkeyPatch) -> None:
        self.ops = ops
        self.media = media
        self.scripts: list[dict[str, Any]] = [script()]
        self.providers: list[FakeProvider] = []
        self.environment_on = True
        injected = Allowlist(frozenset({FakeMedia.HOST}))
        defaults = dict(sbm.swedebeat_step.__kwdefaults__ or {})
        defaults["client_factory"] = lambda: mowl_media_test_client(injected, media)
        defaults["index_poll_seconds"] = 0.01
        monkeypatch.setattr(sbm.swedebeat_step, "__kwdefaults__", defaults)

    def provider(self) -> FakeProvider:
        made = from_dict(self.scripts[min(len(self.providers), len(self.scripts) - 1)])
        self.providers.append(made)
        return made

    def configure(self) -> None:
        design.configure(
            design.Runtime(
                keyring=self.ops.keyring,
                models=FactoryModels(self.provider),
                agent=CONFIG,
                swedebeat_enabled=self.environment_on,
            )
        )

    async def run_next(self, kinds: tuple[str, ...] = (design.KIND, REVISE_KIND)) -> UUID:
        self.configure()
        registry = KindRegistry()
        registry.register(design.DESIGN_DRAFT)
        registry.register(design.REVISE_DRAFT)
        lease = self.ops.app.settings.job_lease_seconds
        row = await queue.claim(self.ops.factory, "a4-w", lease, list(kinds))
        assert row is not None, "nothing to claim"
        await execute(
            Execution(row), self.ops.factory, registry, RunConfig(lease_seconds=lease),
            asyncio.Event(),
        )  # fmt: skip
        return row.job_id

    def sent(self) -> str:
        parts: list[str] = []
        for made in self.providers:
            for s in made.sent:
                spec = s.spec
                parts += [spec.system, spec.first_message]
                for results, reminder in s.turns:
                    parts += [r.content for r in results] + [reminder or ""]
        return "\n".join(parts)

    def analyses(self, owner: UUID) -> int:
        return self.ops.sql(
            "SELECT count(*) FROM swedebeat_analyses WHERE owner_id = :o", o=owner
        ).scalar_one()


@pytest.fixture
def menv(xenv: OpsEnv, mowl_api: FakeMowl, monkeypatch: pytest.MonkeyPatch) -> Iterator[MixEnv]:
    made = MixEnv(xenv, mowl_api.media, monkeypatch)
    try:
        yield made
    finally:
        design.configure(None)


async def rider(browsers: Any, menv: MixEnv, token: str = TOKEN_A) -> Browser:
    browser: Browser = await browsers()
    menv.ops.app.app.dependency_overrides.clear()  # the real registry, as in production
    await menv.ops.connect(browser.user_id, token)  # a reconnect in place: the same id
    return browser


def add_mix(mowl_api: FakeMowl, token: str = TOKEN_A, **kwargs: Any) -> str:
    options = {"mix_id": MIX_ID, "listed_seconds": SECONDS, "link": "beats", **kwargs}
    return mowl_api.add_mix(MIX_NAME, token=token, **options)


def design_body(browser: Browser, mix_id: str = MIX_ID) -> dict[str, Any]:
    return {
        "kind": design.KIND,
        "input": {
            "title": "Mix ride",
            "brief": BRIEF,
            "music": {"source": "mowl_swedebeat_mix", "mix_id": mix_id},
            "mowl_connection_id": str(browser.connection_id),
        },
    }


async def create(browser: Browser, body: dict[str, Any]) -> Any:
    return await browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


def jobs_of(menv: MixEnv, owner: UUID) -> int:
    return menv.ops.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner).scalar_one()


def revisions(menv: MixEnv, job_id: UUID) -> list[Any]:
    return menv.ops.sql(
        "SELECT * FROM class_revisions WHERE job_id = :j ORDER BY number", j=job_id
    ).all()


def flag(menv: MixEnv, value: str | None) -> None:
    menv.ops.sql("DELETE FROM app_settings WHERE key = 'swedebeat_enabled'")
    if value is not None:
        menv.ops.sql(
            "INSERT INTO app_settings (key, value) VALUES ('swedebeat_enabled', CAST(:v AS jsonb))",
            v=json.dumps(value),
        )


# --- designed and saved, end to end ---------------------------------------------------------------


async def test_a_class_is_designed_on_a_fake_mix_and_saved_to_mowl(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl, monkeypatch: pytest.MonkeyPatch
) -> None:
    browser = await rider(browsers, menv)
    add_mix(mowl_api)
    created = await create(browser, design_body(browser))
    assert created.status_code == 201, created.text
    job_id = UUID(created.json()["id"])
    await menv.run_next()
    row = menv.ops.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    owner = browser.user_id

    # The analysis: one new one, one media fetch, the map cached; the link is the mix's.
    assert len(menv.media.requests) == 1
    assert menv.analyses(owner) == 1
    [stored_map] = menv.ops.sql(
        "SELECT map, playlist_id FROM swedebeat_mix_maps WHERE owner_id = :o", o=owner
    ).all()
    [link] = menv.ops.sql(
        "SELECT id, playlist_id FROM mowl_playlist_links WHERE owner_id = :o AND source = "
        "'swedebeat' AND source_id = :m",
        o=owner, m=MIX_ID,
    ).all()  # fmt: skip
    assert link.playlist_id == stored_map.playlist_id

    # The revision's music: provenance and the map's fingerprint; no MOWL playlist id, no map.
    [revision] = revisions(menv, job_id)
    music = revision.music
    assert music == {
        "source": "mowl_swedebeat_mix",
        "mix_id": MIX_ID,
        "name": MIX_NAME,
        "duration": pytest.approx(SECONDS, abs=1.0),
        "listed_duration": SECONDS,
        "unsure_beat_one": music["unsure_beat_one"],
        "fingerprint": sbm.fingerprint(stored_map.map),
        "link_id": str(link.id),
    }
    assert str(link.playlist_id) not in json.dumps(music)
    assert revision.provenance["crossfade_sec"] == 0.0
    music_source = menv.ops.sql(
        "SELECT music_source FROM classes WHERE id = :c", c=row.class_id
    ).scalar_one()
    assert music_source == {"source": "mowl_swedebeat_mix", "mix_id": MIX_ID}

    # What the model was sent: the songs' numbers, never the mix's name, ids or the token.
    sent = menv.sent()
    assert '"bpm":' in sent
    assert '"tracks":' in sent
    for value in (MIX_NAME, MIX_ID, str(link.playlist_id), TOKEN_A, str(browser.connection_id),
                  str(link.id), str(owner)):  # fmt: skip
        assert value not in sent, value
    assert "Spotify's crossfade at 10 s" not in sent  # the Spotify timing warning does not apply

    # Save to MOWL, unchanged: the playlist comes from the user's link row the revision names.
    programs = FakeMowlPrograms()
    monkeypatch.setattr(mowl_lib, "API", "https://api.mowl.com/")
    opener = build_allowlist_opener(WORKER_ALLOWLIST, https_handler=programs)
    monkeypatch.setattr(urllib.request, "_opener", opener)
    saving.configure(saving.Runtime(menv.ops.keyring))
    try:
        body = {
            "kind": saving.KIND,
            "input": {
                "class_id": str(row.class_id),
                "revision_id": str(revision.id),
                "content_hash": bytes(revision.content_hash).hex(),
                "mowl_connection_id": str(browser.connection_id),
            },
        }
        saved = await create(browser, body)
        assert saved.status_code == 201, saved.text
        save_id = UUID(saved.json()["id"])
        registry = KindRegistry()
        registry.register(saving.SAVE_WORKOUT)
        lease = menv.ops.app.settings.job_lease_seconds
        claimed = await queue.claim(menv.ops.factory, "a4-save", lease, [saving.KIND])
        assert claimed is not None
        await execute(
            Execution(claimed), menv.ops.factory, registry, RunConfig(lease_seconds=lease),
            asyncio.Event(),
        )  # fmt: skip
    finally:
        saving.configure(None)
    assert menv.ops.row(save_id).state == "completed"
    [program] = programs.programs.values()
    assert program.token == TOKEN_A
    assert program.body["PlaylistID"] == link.playlist_id  # the mix's MOWL playlist
    assert program.public is False
    [saved_row] = menv.ops.sql("SELECT * FROM saved_workouts WHERE owner_id = :o", o=owner).all()
    assert (saved_row.program_id, saved_row.playlist_id) == (program.id, link.playlist_id)


async def test_a_second_design_on_the_mix_uses_the_cache_and_costs_no_analysis(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, menv)
    add_mix(mowl_api)
    for _ in range(2):
        created = await create(browser, design_body(browser))
        assert created.status_code == 201, created.text
        await menv.run_next()
        assert menv.ops.row(UUID(created.json()["id"])).state == "completed"
    assert len(menv.media.requests) == 1
    assert menv.analyses(browser.user_id) == 1
    assert len(mowl_api.attaches) == 1


async def test_the_empty_production_list_says_the_mix_was_added_to_mowl_but_not_analysed(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0016 section 4: with the real (empty) media list an uncached mix is attached into the
    user's MOWL account, then ends ``media_host_not_allowed`` with no media request; the page's
    sentence says so."""
    defaults = dict(sbm.swedebeat_step.__kwdefaults__ or {})
    defaults["client_factory"] = sbm.mowl_media_async_client  # the production client
    monkeypatch.setattr(sbm.swedebeat_step, "__kwdefaults__", defaults)
    browser = await rider(browsers, menv)
    add_mix(mowl_api)
    created = await create(browser, design_body(browser))
    job_id = UUID(created.json()["id"])
    await menv.run_next()
    row = menv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "media_host_not_allowed")
    assert mowl_api.attaches == [MIX_ID]  # added to MOWL, as the sentence says
    assert menv.media.requests == []
    assert all(p.sent == [] for p in menv.providers)  # no model call
    shown = (await browser.get(f"/api/jobs/{job_id}")).json()
    assert "added to your MOWL account" in shown["error_message"]
    assert "could not be analysed" in shown["error_message"]


# --- admit: the flag, the MOWL connection, the user's own listing ---------------------------------


async def test_admit_refuses_a_mix_outside_the_users_own_listing(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, menv)
    add_mix(mowl_api, mix_id="mine-0001")
    theirs = add_mix(mowl_api, token=TOKEN_B, mix_id="theirs-0001")
    for wanted in (theirs, "made-up-id"):
        refused = await create(browser, design_body(browser, wanted))
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["reason"] == "invalid_input"
    assert jobs_of(menv, browser.user_id) == 0
    assert mowl_api.attaches == []
    assert menv.media.requests == []
    # The id's shape is the input model's: a URL never reaches admit.
    url = await create(browser, design_body(browser, "https://media.fake.test/mix.mp3"))
    assert url.status_code == 422


@pytest.mark.parametrize("off", ["database", "environment"])
async def test_admit_refuses_feature_off_before_anything_is_read(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl, off: str
) -> None:
    browser = await rider(browsers, menv)
    add_mix(mowl_api)
    if off == "database":
        flag(menv, "off")
    else:
        mixadmit.configure(replace(mixadmit.current(), enabled=False))
    try:
        refused = await create(browser, design_body(browser))
    finally:
        flag(menv, None)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "feature_off"
    assert mowl_api.list_calls == 0  # no listing, no MOWL request at all
    assert jobs_of(menv, browser.user_id) == 0


async def test_admit_refuses_without_the_users_mowl_connection(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, menv)
    add_mix(mowl_api)
    menv.ops.sql(
        "UPDATE connections SET status = 'needs_reconnect' WHERE owner_id = :o", o=browser.user_id
    )
    refused = await create(browser, design_body(browser))
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "not_connected"
    assert mowl_api.list_calls == 0


async def test_a_spotify_playlist_is_admitted_as_before_with_swedebeat_off(
    browsers: Any, menv: MixEnv
) -> None:
    """A user with no Swedebeat designs as before: the Spotify source never reads the flag."""
    browser = await rider(browsers, menv)
    flag(menv, "off")
    try:
        body = design_body(browser)
        body["input"]["music"] = {
            "source": "mowl_spotify_playlist",
            "spotify_playlist_id": "37i9dQZF1DXcBWIGoYBM5M",
        }
        created = await create(browser, body)
    finally:
        flag(menv, None)
    assert created.status_code == 201, created.text
    stored = menv.ops.sql(
        "SELECT input FROM jobs WHERE id = :j", j=created.json()["id"]
    ).scalar_one()
    assert stored["music"] == body["input"]["music"]


# --- a queued job whose flag went off -------------------------------------------------------------


@pytest.mark.parametrize("off", ["database", "worker"])
async def test_a_stale_job_ends_feature_off_and_calls_nothing(
    menv: MixEnv, mowl_api: FakeMowl, off: str
) -> None:
    owner = await menv.ops.user()
    connection = await menv.ops.connect(owner, TOKEN_A)
    add_mix(mowl_api)
    body = {
        "title": "Mix ride",
        "brief": BRIEF,
        "music": {"source": "mowl_swedebeat_mix", "mix_id": MIX_ID},
        "mowl_connection_id": str(connection),
    }
    job_id = new_job(menv.ops.admin, owner, kind=design.KIND, input=json.dumps(body))
    if off == "database":
        flag(menv, "off")
    else:
        menv.environment_on = False
    try:
        await menv.run_next()
    finally:
        flag(menv, None)
    row = menv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "feature_off")
    assert mowl_api.requests == []
    assert menv.media.requests == []
    assert menv.providers == []
    assert menv.analyses(owner) == 0


# --- the daily limit of new analyses ------------------------------------------------------------


def plant_analyses(menv: MixEnv, owner: UUID, n: int, hours_ago: float = 1.0) -> None:
    for _ in range(n):
        menv.ops.sql(
            "INSERT INTO swedebeat_analyses (owner_id, created_at) "
            "VALUES (:o, now() - make_interval(secs => :s))",
            o=owner, s=hours_ago * 3600,
        )  # fmt: skip


async def test_admit_refuses_an_eleventh_new_analysis_but_not_a_cached_mix(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, menv)
    add_mix(mowl_api)
    other = add_mix(mowl_api, mix_id="other-0001")
    # One analysis made (the cache), then nine more: ten in the window.
    created = await create(browser, design_body(browser))
    assert created.status_code == 201, created.text
    await menv.run_next()
    plant_analyses(menv, browser.user_id, mixlimit.ANALYSES_PER_DAY - 1)
    plant_analyses(menv, browser.user_id, 5, hours_ago=25)  # outside the window: not counted
    refused = await create(browser, design_body(browser, other))
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == {"reason": "analysis_limit", "limit": 10, "used": 10}
    cached = await create(browser, design_body(browser, MIX_ID))
    assert cached.status_code == 201, cached.text


async def test_the_worker_enforces_the_limit_before_anything_is_attached_or_fetched(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, menv)
    add_mix(mowl_api)
    plant_analyses(menv, browser.user_id, mixlimit.ANALYSES_PER_DAY - 1)
    created = await create(browser, design_body(browser))  # nine used: admitted
    assert created.status_code == 201, created.text
    plant_analyses(menv, browser.user_id, 1)  # another job took the tenth meanwhile
    await menv.run_next()
    row = menv.ops.row(UUID(created.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "analysis_limit")
    assert mowl_api.attaches == []
    assert mowl_api.list_calls == 1  # admit's listing only
    assert menv.media.requests == []
    assert menv.analyses(browser.user_id) == mixlimit.ANALYSES_PER_DAY


async def test_the_tenth_analysis_is_allowed_and_counted_even_when_its_fetch_fails(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, menv)
    add_mix(mowl_api, link="status", code=403)
    plant_analyses(menv, browser.user_id, mixlimit.ANALYSES_PER_DAY - 1)
    created = await create(browser, design_body(browser))
    assert created.status_code == 201, created.text
    await menv.run_next()
    row = menv.ops.row(UUID(created.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "media_refused")
    assert menv.analyses(browser.user_id) == mixlimit.ANALYSES_PER_DAY  # the failure counted


# --- revise on the mix, and Save's link checks ----------------------------------------------------


async def test_a_class_on_a_mix_is_revised_on_its_cached_map(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, menv)
    add_mix(mowl_api)
    created = await create(browser, design_body(browser))
    await menv.run_next()
    job_id = UUID(created.json()["id"])
    [base] = revisions(menv, job_id)
    menv.scripts = [revise_script()]
    menv.providers = []
    ask = {
        "kind": REVISE_KIND,
        "input": {
            "class_id": str(menv.ops.row(job_id).class_id),
            "base_revision_id": str(base.id),
            "request": "Make the push harder",
            "mowl_connection_id": str(browser.connection_id),
        },
    }
    flag(menv, "off")
    try:
        refused = await create(browser, ask)
    finally:
        flag(menv, None)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "feature_off"
    revised = await create(browser, ask)
    assert revised.status_code == 201, revised.text
    await menv.run_next()
    revise_id = UUID(revised.json()["id"])
    assert menv.ops.row(revise_id).state == "completed"
    [new] = revisions(menv, revise_id)
    assert new.parent_id == base.id
    assert new.music == base.music  # the same mix, map and link
    assert len(menv.media.requests) == 1  # the cache: no second fetch
    assert MIX_NAME not in menv.sent()


async def test_save_refuses_a_mix_revision_whose_link_is_gone_or_of_another_source(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, menv)
    add_mix(mowl_api)
    created = await create(browser, design_body(browser))
    await menv.run_next()
    job_id = UUID(created.json()["id"])
    [revision] = revisions(menv, job_id)
    body = {
        "kind": saving.KIND,
        "input": {
            "class_id": str(menv.ops.row(job_id).class_id),
            "revision_id": str(revision.id),
            "content_hash": bytes(revision.content_hash).hex(),
            "mowl_connection_id": str(browser.connection_id),
        },
    }
    # A Spotify link row with the mix revision's link id is not its link.
    menv.ops.sql(
        "UPDATE mowl_playlist_links SET source = 'spotify', source_id = '37i9dQZF1DXcBWIGoYBM5M' "
        "WHERE owner_id = :o",
        o=browser.user_id,
    )
    wrong = await create(browser, body)
    assert wrong.status_code == 409, wrong.text
    assert wrong.json()["detail"]["reason"] == "music_unlinked"
    menv.ops.sql("DELETE FROM mowl_playlist_links WHERE owner_id = :o", o=browser.user_id)
    gone = await create(browser, body)
    assert gone.json()["detail"]["reason"] == "music_unlinked"
    assert str(uuid.UUID(revision.music["link_id"])) == revision.music["link_id"]
