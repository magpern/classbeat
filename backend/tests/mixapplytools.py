"""Helpers of the mix-choice tests (ADR 0036 amendment B, #1369): a designed studio class whose
proposal the test chooses, the read-only preview, the ``apply_mix_choices`` job through the jobs
API and its run, and the rows that must not change. Fakes only (Spotify, the SwedeBeat gateway,
``FakeProvider``, spotdown); never the network."""

import asyncio
import copy
import json
from typing import Any
from uuid import UUID

import httpx

from agentfake import good_draft
from classbeat.design import revision
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.swedebeat_studio.apply_kind import APPLY_MIX_CHOICES, KIND
from songtools import new_key
from studiotools import StudioEnv, StudioRider, pick_script
from test_swedebeat_studio_design import approved, design_script, run_kind, start_design

LENGTH = 240.0
# Every odd track cut at its end (223.53 s once snapped), crossfades 8: composes to 1798.12 s.
TRIMMED = {
    "tracks": [
        {"track": n, "start_sec": 0, "end_sec": 224.0 if n % 2 else LENGTH} for n in range(8)
    ],
    "crossfades": [8] * 7,
}
WHOLE = {"dj_mix": False, "blend": False, "trim": False}
DJ = {"dj_mix": True, "blend": True, "trim": True}
NO_MODEL: dict[str, Any] = {"model": "fake-model", "turns": []}  # any model call fails the run
TABLES = (
    "class_revisions",
    "jobs",
    "agent_runs",
    "credit_ledger",
    "class_messages",
    "job_events",
    "swedebeat_analyses",
)


def workout(total: int = 1798, game: bool = False) -> dict[str, Any]:
    """The fake's good draft with its last interval shortened to make ``total`` seconds, and
    with ``game`` a Power Pull in its second recovery."""
    made = copy.deepcopy(good_draft())
    last = made["segments"][-1]["intervals"][-1]
    last["sec"] += total - sum(i["sec"] for s in made["segments"] for i in s["intervals"])
    if game:
        easy = made["segments"][5]["intervals"][0]
        easy.update({"sec": 90, "zone": 4, "rpm": "beat", "game": "power_pull"})
    return made


async def designed(
    studio: StudioEnv, mix: dict[str, Any] | None = None, draft: dict[str, Any] | None = None
) -> tuple[StudioRider, UUID, UUID]:
    """A studio class designed on ``mix`` with ``draft``: the rider, the class and its draft."""
    studio.songs.script = pick_script()
    rider, class_id, list_id = await approved(studio)
    script = design_script(mix or TRIMMED)
    script["drafts"]["good"] = draft or workout()
    studio.songs.script = script
    response = await start_design(rider, class_id, list_id)
    assert response.status_code == 201, response.text
    row = await run_kind(studio, UUID(response.json()["job_id"]))
    assert (row.state, row.error_category) == ("completed", None)
    return rider, class_id, current(studio, class_id)


def current(studio: StudioEnv, class_id: UUID) -> UUID:
    return studio.songs.sql(
        "SELECT current_revision_id FROM classes WHERE id = :c", c=class_id
    ).scalar_one()


def digest(studio: StudioEnv, revision_id: UUID) -> str:
    return bytes(studio.songs.revision(revision_id).content_hash).hex()


async def preview(
    rider: StudioRider, class_id: UUID, revision_id: UUID, content_hash: str, choices: Any
) -> httpx.Response:
    body = {"revision_id": str(revision_id), "content_hash": content_hash, "choices": choices}
    return await rider.browser.post(f"/api/classes/{class_id}/mix-choices/preview", body)


async def preview_of(studio: StudioEnv, rider: StudioRider, class_id: UUID, choices: Any) -> Any:
    at = current(studio, class_id)
    answer = await preview(rider, class_id, at, digest(studio, at), choices)
    assert answer.status_code == 200, answer.text
    return answer.json()


async def create(
    studio: StudioEnv, rider: StudioRider, class_id: UUID, choices: Any, base: UUID | None = None
) -> httpx.Response:
    at = base or current(studio, class_id)
    body = {
        "kind": KIND,
        "input": {
            "class_id": str(class_id),
            "base_revision_id": str(at),
            "base_content_hash": digest(studio, at),
            "choices": choices,
        },
    }
    return await rider.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


async def run_apply(studio: StudioEnv, job_id: UUID) -> Any:
    songs = studio.songs
    songs.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)
    lease = songs.spot.env.settings.job_lease_seconds
    registry = KindRegistry()
    registry.register(APPLY_MIX_CHOICES)
    row = await queue.claim(songs.factory, "apply-w", lease, [KIND])
    assert row is not None, "nothing to claim"
    await execute(
        Execution(row),
        songs.factory,
        registry,
        RunConfig(lease_seconds=lease, spotify_calls_per_job=songs.calls_per_job),
        asyncio.Event(),
    )
    return songs.row(job_id)


async def applied(studio: StudioEnv, rider: StudioRider, class_id: UUID, choices: Any) -> Any:
    """Create and run an apply; the stored revision row."""
    studio.songs.script = NO_MODEL
    created = await create(studio, rider, class_id, choices)
    assert created.status_code == 201, created.text
    row = await run_apply(studio, UUID(created.json()["id"]))
    assert (row.state, row.error_category) == ("completed", None)
    return studio.songs.revision(current(studio, class_id))


def counts(studio: StudioEnv, owner: UUID) -> dict[str, int]:
    return {
        t: studio.songs.sql(f"SELECT count(*) FROM {t} WHERE owner_id = :o", o=owner).scalar_one()  # noqa: S608 - constant names
        for t in TABLES
    }


def copy_revision(
    studio: StudioEnv,
    source: UUID,
    origin: str,
    parent: UUID | None,
    content: dict[str, Any] | None = None,
) -> UUID:
    """A copy of ``source`` with another provenance origin and parent (and, with ``content``,
    that content under its own studio hash), made the class's current draft: a stand-in for
    kinds the studio cannot reach (a duplicate, a beat-one refit, a draft stored with a game
    MOWL has since retired)."""
    row = studio.songs.revision(source)
    content = content if content is not None else row.content
    content_hash = revision.studio_hash(content, row.music["mix_spec"])
    made = studio.songs.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, content_hash, "
        "music, provenance, job_id, parent_id) SELECT gen_random_uuid(), r.owner_id, r.class_id, "
        "(SELECT max(n.number) + 1 FROM class_revisions n WHERE n.class_id = r.class_id), r.kind, "
        "CAST(:content AS jsonb), :hash, r.music, "
        "jsonb_set(r.provenance, '{origin}', to_jsonb(CAST(:origin AS text))), r.job_id, "
        "CAST(:parent AS uuid) FROM class_revisions r WHERE r.id = :source RETURNING id, class_id",
        content=json.dumps(content),
        hash=content_hash,
        origin=origin,
        parent=parent,
        source=source,
    ).one()
    studio.songs.sql(
        "UPDATE classes SET current_revision_id = :r WHERE id = :c", r=made.id, c=made.class_id
    )
    return made.id
