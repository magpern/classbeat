"""The six actions that would write or reuse a legacy Ghost Target draft refuse it with
``game_retired`` (ADR 0029 section 3, MA1a-1; issue #877): Make current, Save, Update, Refit,
Change songs and Duplicate. Each refusal comes before the save path looks up the library's
``GAMES`` and writes nothing; Save, Update and Refit also refuse it in the worker (a job queued
before the refusal existed) without a request to MOWL.

Against Postgres as ``classbeat_app`` through the real jobs and class APIs; the fake MOWL, the fake
Spotify and ``FakeProvider`` for the classes these tests design first. Never the network.
"""

import asyncio
import copy
import json
import uuid
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.design.kind import DESIGN_DRAFT
from classbeat.design.refit import KIND as REFIT_KIND
from classbeat.design.refit import REFIT_DRAFT
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from jobapi import Browser, new_key
from jobtools import new_class, new_job
from legacytools import content_hash, legacy_child
from revisesongtools import change, picked
from revisetools import ReviseEnv
from savetools import DRAFT, FakeMowlPrograms, SaveEnv
from songtools import SongEnv
from updatetools import UpdateEnv

RETIRED = "game_retired"
# ``savetools.DRAFT`` with its Racetrack game stored as Ghost Target (the same length and zone).
LEGACY: dict[str, Any] = copy.deepcopy(DRAFT)
LEGACY["segments"][1]["intervals"][2]["game"] = "ghost_target"
BRIEF: dict[str, Any] = {"length_min": 20, "intensity": "moderate"}
# A design's provenance as stored (a duplicate copies and validates its descriptive fields).
PROVENANCE: dict[str, Any] = {
    "provider": "fake", "model": "m", "model_answered": "m", "effort": "medium",
    "prompt_version": "a" * 64, "classbeat_version": "0.0.0", "mowl_version": "0.11.0",
    "map_version": 1, "crossfade_sec": 0.0, "agent_run_ids": [str(uuid.uuid4())],
    "usage": {"input_tokens": 5, "output_tokens": 6, "cache_read_tokens": 0,
              "cache_write_tokens": 0},
    "cost_micros": 1, "origin": "design_draft", "tss_approximate": False,
}  # fmt: skip


def refused(response: Any, reason: str = RETIRED) -> None:
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["reason"] == reason


def jobs_of(sql: Any, owner: UUID) -> int:
    return sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner).scalar_one()


# --- Save and Update ------------------------------------------------------------------------------


async def test_save_refuses_a_legacy_revision_at_creation_and_in_the_worker(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    saved = await senv.saved_class(content=LEGACY)
    browser = await save_browser(saved)
    before = jobs_of(senv.sql, saved.owner)
    refused(await browser.save())
    assert jobs_of(senv.sql, saved.owner) == before  # nothing created
    job_id = senv.job(saved)  # as if queued before the refusal existed
    await senv.run_next()
    row = senv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert senv.operations(saved.owner) == []
    assert (len(fake_programs.requests), len(fake_programs.writes)) == (0, 0)


async def test_update_refuses_a_legacy_revision_at_creation_and_in_the_worker(
    uenv: UpdateEnv, update_browser: Any
) -> None:
    live = await uenv.live()  # a class saved by a real Save job
    revision = uenv.revision(live, LEGACY)
    browser = await update_browser(live)
    before, counts = jobs_of(uenv.sql, live.saved.owner), uenv.counts()
    refused(await browser.update(revision, LEGACY))
    assert jobs_of(uenv.sql, live.saved.owner) == before
    job_id = uenv.job(live, revision, LEGACY)
    await uenv.run_next()
    row = uenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert uenv.operations(live.saved.owner) == []
    assert uenv.counts() == counts  # no write and no read of the program


# --- Make current and Duplicate -------------------------------------------------------------------


class Plant:
    """A user's class with a stored brief and music source: r1 (a modern draft with a game,
    current) and r2 (the same draft stored with Ghost Target)."""

    def __init__(self, admin: Connection, browser: Browser) -> None:
        self.admin, self.browser, self.owner = admin, browser, browser.user_id
        self.class_id = new_class(admin, self.owner)
        self.sql(
            "UPDATE classes SET brief = CAST(:b AS jsonb), music_source = CAST(:m AS jsonb) "
            "WHERE id = :c",
            b=json.dumps(BRIEF), c=self.class_id,
            m=json.dumps({"source": "mowl_spotify_playlist",
                          "spotify_playlist_id": "37i9dQZF1DXcBWIGoYBM5M"}),
        )  # fmt: skip
        self.r1 = self.plant(1, DRAFT)
        self.r2 = self.plant(2, LEGACY)
        self.sql("UPDATE classes SET current_revision_id = :r WHERE id = :c", r=self.r1,
                 c=self.class_id)  # fmt: skip

    def sql(self, statement: str, **params: Any) -> Any:
        return self.admin.execute(text(statement), params)

    def plant(self, number: int, content: dict[str, Any]) -> UUID:
        music = {"source": "mowl_spotify_playlist", "link_id": str(uuid.uuid4()),
                 "fingerprint": "ab" * 32, "unknown_bpm": []}  # fmt: skip
        return self.sql(
            "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
            "content_hash, music, provenance) VALUES (:id, :o, :c, :n, 'draft', "
            "CAST(:content AS jsonb), :h, CAST(:music AS jsonb), CAST(:p AS jsonb)) RETURNING id",
            id=uuid.uuid4(), o=self.owner, c=self.class_id, n=number,
            content=json.dumps(content), h=content_hash(content), music=json.dumps(music),
            p=json.dumps(PROVENANCE),
        ).scalar_one()  # fmt: skip

    def current(self) -> UUID:
        return self.sql(
            "SELECT current_revision_id FROM classes WHERE id = :c", c=self.class_id
        ).scalar_one()

    def classes(self) -> int:
        return self.sql("SELECT count(*) FROM classes WHERE owner_id = :o", o=self.owner).scalar()


@pytest.fixture
async def plant(browsers: Any, jobs_admin: Connection) -> Plant:
    return Plant(jobs_admin, await browsers(mowl=False))


async def test_make_current_refuses_a_legacy_revision_and_changes_nothing(plant: Plant) -> None:
    path = f"/api/classes/{plant.class_id}/current"
    legacy = {"revision_id": str(plant.r2), "content_hash": content_hash(LEGACY).hex()}
    refused(await plant.browser.post(path, legacy))
    assert plant.current() == plant.r1
    # A modern draft with one of the four games is picked as before (the plan includes games).
    other = DRAFT | {"name": "Another"}
    modern = plant.plant(3, other)
    body = {"revision_id": str(modern), "content_hash": content_hash(other).hex()}
    picked_now = await plant.browser.post(path, body)
    assert picked_now.status_code == 200, picked_now.text
    assert plant.current() == modern


async def test_duplicate_refuses_a_legacy_revision_with_a_409_not_a_500(plant: Plant) -> None:
    path = f"/api/classes/{plant.class_id}/duplicate"
    before = plant.classes()
    body = {"revision_id": str(plant.r2), "content_hash": content_hash(LEGACY).hex(), "title": "x"}
    refused(await plant.browser.post(path, body, {"Idempotency-Key": new_key()}))
    assert plant.classes() == before  # no class, no revision, no credit
    modern = {"revision_id": str(plant.r1), "content_hash": content_hash(DRAFT).hex(), "title": "y"}
    made = await plant.browser.post(path, modern, {"Idempotency-Key": new_key()})
    assert made.status_code == 201, made.text


# --- Refit ----------------------------------------------------------------------------------------


async def _run_refit(renv: ReviseEnv) -> None:
    settings = renv.denv.app.settings
    registry = KindRegistry()
    for kind in (DESIGN_DRAFT, REFIT_DRAFT):
        registry.register(kind)
    claimed = await queue.claim(
        renv.denv.ops.factory, "ma1-w", settings.job_lease_seconds, [REFIT_KIND]
    )
    assert claimed is not None, "nothing to claim"
    await execute(
        Execution(claimed),
        renv.denv.ops.factory,
        registry,
        RunConfig(lease_seconds=settings.job_lease_seconds),
        asyncio.Event(),
    )


@pytest.mark.usefixtures("jenv")
async def test_refit_refuses_a_legacy_base_at_creation_and_in_the_worker(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    legacy, content, _ = legacy_child(renv.sql, class_id, base)
    values = {
        "class_id": str(class_id),
        "base_revision_id": str(legacy),
        "mowl_connection_id": str(renv.browser.connection_id),
    }
    before = jobs_of(renv.sql, renv.owner)
    made = await renv.browser.post(
        "/api/jobs", {"kind": REFIT_KIND, "input": values}, {"Idempotency-Key": new_key()}
    )
    refused(made)
    assert jobs_of(renv.sql, renv.owner) == before
    stored = {**values, "base_revision_hash": content_hash(content).hex()}
    job_id = new_job(
        renv.denv.ops.admin, renv.owner, class_id, kind=REFIT_KIND,
        input=json.dumps(stored), credit_state="none",
    )  # fmt: skip
    await _run_refit(renv)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert [r.id for r in renv.revisions(class_id)] == [base, legacy]  # nothing stored


# --- Change songs ---------------------------------------------------------------------------------


async def test_change_songs_refuses_a_legacy_base_and_sends_nothing(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    legacy, _, _ = legacy_child(songenv.sql, class_id, base)
    before, counts = jobs_of(songenv.sql, rider.user_id), songenv.counts()
    refused(await change(rider, class_id, legacy))
    assert (jobs_of(songenv.sql, rider.user_id), songenv.counts()) == (before, counts)
    said = songenv.sql("SELECT count(*) FROM class_messages WHERE class_id = :c", c=class_id)
    assert said.scalar_one() == 0
