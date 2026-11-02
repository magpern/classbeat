"""ADR 0016 section 8 and A6: the Swedebeat mix ownership, secrecy and flag-off done-when suite.

Fakes only, no network. The positive media fetch and child-process analysis stay in
``test_swedebeat_fetch.py`` and ``test_swedebeat_media_fetch.py``; Playwright covers path (a) on
the fakes with the production empty ``MOWL_MEDIA_HOSTS`` in ``e2e/tests/swedebeat-mix.spec.ts``.
"""

import io
import json
import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from authtools import Env, running_env
from classbeat import design
from classbeat.db import user_session
from classbeat.design import mixfetch
from classbeat.design import swedebeat_music as sbm
from classbeat.log import QUIET_LOGGERS, configure_logging
from ctx import make_context
from designtools import BRIEF
from jobapi import Browser
from jobtools import new_job
from mowlfake import FakeMowl
from optools import TOKEN_A, TOKEN_B, OpsEnv
from swedebeatcore import FakeSwedebeat
from swedebeatfake import PASSWORD
from test_mowl_redaction import Everything
from test_swedebeat_design import (
    MIX_ID,
    MixEnv,
    add_mix,
    create,
    design_body,
    flag,
    jobs_of,
    rider,
)
from test_swedebeat_fetch import Harness, add_beats

pytest_plugins = ["test_mowl_redaction"]

pytestmark = pytest.mark.usefixtures("jenv", "mowl_api")

MIXES_URL = "/api/music/swedebeat-mixes"
SAME_ID = "shared-mix-a6-001"
PLANTED_APP_KEY = "AKIA" + "0123456789012345"
FOREIGN_MIX = "foreign-mix-0001"


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url, swedebeat_enabled=True) as running:
        yield running


@pytest.fixture(autouse=True)
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "audio-root"
    root.mkdir()
    monkeypatch.setattr(mixfetch, "work_root", lambda: root)
    return root


@pytest.fixture
def mixenv(xenv: OpsEnv, mowl_api: FakeMowl, monkeypatch: pytest.MonkeyPatch) -> Iterator[MixEnv]:
    made = MixEnv(xenv, mowl_api.media, monkeypatch)
    try:
        yield made
    finally:
        design.configure(None)


@pytest.fixture
def harness(xenv: Any, mowl_api: FakeMowl) -> Harness:
    return Harness(xenv, mowl_api.media)


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


# --- ownership (T108) ---------------------------------------------------------------------------


async def test_two_users_never_share_a_mix_map_or_link(
    browsers: Any, mixenv: MixEnv, mowl_api: FakeMowl
) -> None:
    alice = await rider(browsers, mixenv, TOKEN_A)
    bob = await rider(browsers, mixenv, TOKEN_B)
    mowl_api.add_mix("Alice mix", token=TOKEN_A, mix_id=SAME_ID)
    mowl_api.add_mix("Bob mix", token=TOKEN_B, mix_id=SAME_ID)
    for browser, name in ((alice, "Alice mix"), (bob, "Bob mix")):
        listed = (await browser.get(MIXES_URL)).json()["mixes"]
        assert [m["name"] for m in listed] == [name]
    add_mix(mowl_api, token=TOKEN_A, mix_id=FOREIGN_MIX)
    refused = await create(bob, design_body(bob, FOREIGN_MIX))
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "invalid_input"
    assert jobs_of(mixenv, bob.user_id) == 0


async def test_rls_hides_maps_and_links_from_the_other_user(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner = await harness.ops.user()
    connection = await harness.ops.connect(owner, TOKEN_A)
    other = await harness.ops.user()
    await harness.ops.connect(other, TOKEN_B)
    mix = add_beats(mowl_api, token=TOKEN_A)
    assert (await harness.run(owner, connection, mix)).state == "completed"
    async with user_session(harness.ops.factory, make_context(other)) as db:
        rows = (await db.execute(text("SELECT mix_id FROM swedebeat_mix_maps"))).all()
        assert rows == []
        changed = await db.execute(text("UPDATE swedebeat_mix_maps SET name = 'x'"))
        assert changed.rowcount == 0


# --- secrecy (T109, T101) -----------------------------------------------------------------------


async def test_the_temp_link_signature_and_app_key_are_in_no_log_response_or_dump(
    harness: Harness,
    mowl_api: FakeMowl,
    operator_log: io.StringIO,
    swedebeat_app: dict[str, str],
    captured: Everything,
) -> None:
    owner = await harness.ops.user()
    connection = await harness.ops.connect(owner, TOKEN_A)
    mix = add_beats(mowl_api)
    assert (await harness.run(owner, connection, mix)).state == "completed"
    secrets = [
        PASSWORD,
        swedebeat_app["key"],
        swedebeat_app["iv"],
        PLANTED_APP_KEY,
        *harness.media.signatures,
    ]
    blob = harness.dump() + operator_log.getvalue() + captured.out.getvalue()
    for record in captured.records:
        blob += record.getMessage() + repr(vars(record))
    for secret in secrets:
        assert secret not in blob, secret[:8]
    assert "mix.mp3" not in blob


async def test_mutation_if_a_signature_reached_the_dump_the_suite_would_fail(
    harness: Harness, mowl_api: FakeMowl
) -> None:
    owner = await harness.ops.user()
    connection = await harness.ops.connect(owner, TOKEN_A)
    mix = add_beats(mowl_api)
    assert (await harness.run(owner, connection, mix)).state == "completed"
    blob = harness.dump()
    assert harness.media.signatures
    assert harness.media.signatures[0] not in blob


# --- flag off -----------------------------------------------------------------------------------


async def test_with_the_flag_off_the_mix_route_reads_nothing(
    browsers: Any, env: Env, mowl_api: FakeMowl, superuser: Any
) -> None:
    browser: Browser = await browsers(mowl=True)
    mowl_api.add_mix("Mix", token=TOKEN_A)
    env.app.state.settings = env.settings.model_copy(update={"swedebeat_enabled": False})
    before = len(mowl_api.requests)
    assert (await browser.get(MIXES_URL)).status_code == 404
    assert len(mowl_api.requests) == before
    assert superuser.execute(text("SELECT count(*) FROM swedebeat_mix_maps")).scalar_one() == 0


async def test_m4_style_design_on_a_playlist_touches_no_swedebeat_row_or_host(
    browsers: Any,
    mixenv: MixEnv,
    mowl_api: FakeMowl,
    swedebeat_api: FakeSwedebeat,
    superuser: Any,
) -> None:
    browser = await rider(browsers, mixenv)
    flag(mixenv, "off")
    try:
        body = design_body(browser)
        body["input"]["music"] = {
            "source": "mowl_spotify_playlist",
            "spotify_playlist_id": "37i9dQZF1DXcBWIGoYBM5M",
        }
        created = await create(browser, body)
    finally:
        flag(mixenv, None)
    assert created.status_code == 201, created.text
    swedebeat_calls = len(swedebeat_api.calls)
    assert superuser.execute(text("SELECT count(*) FROM swedebeat_mix_maps")).scalar_one() == 0
    assert len(swedebeat_api.calls) == swedebeat_calls
    assert not any("swedebeat" in r.lower() for r in mowl_api.requests)


async def test_a_stale_mix_job_ends_feature_off_with_no_provider_work(
    mixenv: MixEnv, mowl_api: FakeMowl
) -> None:
    owner = await mixenv.ops.user()
    connection = await mixenv.ops.connect(owner, TOKEN_A)
    add_mix(mowl_api)
    body = {
        "title": "Mix",
        "brief": BRIEF,
        "music": {"source": "mowl_swedebeat_mix", "mix_id": MIX_ID},
        "mowl_connection_id": str(connection),
    }
    job_id = new_job(mixenv.ops.admin, owner, kind=design.KIND, input=json.dumps(body))
    mixenv.environment_on = False
    await mixenv.run_next()
    row = mixenv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "feature_off")
    assert mowl_api.requests == []
    assert mixenv.media.requests == []


async def test_the_empty_production_media_list_refuses_before_any_media_request(
    browsers: Any, mixenv: MixEnv, mowl_api: FakeMowl, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults = dict(sbm.swedebeat_step.__kwdefaults__ or {})
    defaults["client_factory"] = sbm.mowl_media_async_client
    monkeypatch.setattr(sbm.swedebeat_step, "__kwdefaults__", defaults)
    browser = await rider(browsers, mixenv)
    add_mix(mowl_api)
    created = await create(browser, design_body(browser))
    await mixenv.run_next()
    row = mixenv.ops.row(UUID(created.json()["id"]))
    assert row.error_category == "media_host_not_allowed"
    assert mixenv.media.requests == []


async def test_a_cached_map_design_makes_no_swedebeat_mowl_attach_or_link_read(
    browsers: Any, mixenv: MixEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, mixenv)
    add_mix(mowl_api)
    first = await create(browser, design_body(browser))
    assert first.status_code == 201, first.text
    await mixenv.run_next()
    assert mixenv.ops.row(UUID(first.json()["id"])).state == "completed"
    before_attaches = list(mowl_api.attaches)
    before_media = mixenv.media.requests.copy()
    second = await create(browser, design_body(browser))
    assert second.status_code == 201, second.text
    before_worker = len(mowl_api.requests)
    await mixenv.run_next()
    assert mixenv.ops.row(UUID(second.json()["id"])).state == "completed"
    after_worker = mowl_api.requests[before_worker:]
    assert list(mowl_api.attaches) == before_attaches
    assert mixenv.media.requests == before_media
    assert not any(
        "TemporaryLinks" in r or r.startswith("PUT /v1/swedebeat/Playlist") for r in after_worker
    )


async def test_mutation_without_feature_off_would_list_mixes(
    browsers: Any, mixenv: MixEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, mixenv)
    mowl_api.add_mix("Mix", token=TOKEN_A)
    flag(mixenv, "off")
    assert (await browser.get(MIXES_URL)).status_code == 404
    flag(mixenv, "on")
    assert (await browser.get(MIXES_URL)).status_code == 200
