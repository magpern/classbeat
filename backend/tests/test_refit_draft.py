"""The ``refit_draft`` kind (ADR 0007 amendment "beat-one corrections", item 6, BC3).

Through the jobs API, the worker side, Postgres as ``classbeat_app`` and the fake MOWL; no model
is built, so a ``FakeProvider`` script is only used for the class's first design. Never the
network.
"""

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest

from classbeat.design.kind import DESIGN_DRAFT
from classbeat.design.refit import KIND, MAX_REFITS, REFIT_DRAFT
from classbeat.design.revise import REVISE_DRAFT
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from designtools import FakeMowlMusic
from jobapi import new_key
from revisetools import ReviseEnv

pytestmark = pytest.mark.usefixtures("jenv")


def seconds(content: dict[str, Any]) -> list[int]:
    return [i["sec"] for s in content["segments"] for i in s["intervals"]]


async def ask(renv: ReviseEnv, class_id: UUID, base: UUID) -> Any:
    body = {
        "kind": KIND,
        "input": {
            "class_id": str(class_id),
            "base_revision_id": str(base),
            "mowl_connection_id": str(renv.browser.connection_id),
        },
    }
    return await renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


async def run_refit(renv: ReviseEnv) -> None:
    settings = renv.denv.app.settings
    registry = KindRegistry()
    for kind in (DESIGN_DRAFT, REVISE_DRAFT, REFIT_DRAFT):
        registry.register(kind)
    row = await queue.claim(renv.denv.ops.factory, "bc3-w", settings.job_lease_seconds, [KIND])
    assert row is not None, "nothing to claim"
    await execute(
        Execution(row),
        renv.denv.ops.factory,
        registry,
        RunConfig(lease_seconds=settings.job_lease_seconds),
        asyncio.Event(),
    )


async def refit(renv: ReviseEnv, class_id: UUID, base: UUID) -> UUID:
    made = await ask(renv, class_id, base)
    assert made.status_code == 201, made.text
    await run_refit(renv)
    return UUID(made.json()["id"])


def correct_all(renv: ReviseEnv, class_id: UUID, beats: int = 2) -> list[dict[str, int]]:
    base = renv.revisions(class_id)[-1]
    out = []
    for song in base.music["songs"]:
        renv.sql(
            "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats) "
            "VALUES (:o, :c, :f, :s, :b)",
            o=renv.owner, c=class_id, f=base.music["fingerprint"], s=song["song"], b=beats,
        )  # fmt: skip
        out.append({"song": song["song"], "beats": beats})
    return out


async def test_a_refit_stores_the_corrected_draft_as_the_current_child_without_a_model(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    applied = correct_all(renv, class_id)
    built = len(renv.providers)
    job_id = await refit(renv, class_id, base)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category, row.stage, row.credit_state) == (
        "completed", None, "fit", "none",
    )  # fmt: skip
    assert len(renv.providers) == built, "no model provider is built"
    assert renv.denv.agent_runs(job_id) == []
    first, second = renv.revisions(class_id)
    assert (second.number, second.parent_id, second.job_id) == (2, base, job_id)
    assert renv.current(class_id) == second.id
    assert seconds(first.content) != seconds(second.content), "changes moved to the new grid"
    assert sum(seconds(first.content)) == sum(seconds(second.content))
    assert [s["name"] for s in first.content["segments"]] == [
        s["name"] for s in second.content["segments"]
    ], "only the timing changes"
    assert second.music["beat_one"] == applied
    assert second.music["fingerprint"] == first.music["fingerprint"]
    assert second.provenance["origin"] == "beat_one"
    assert second.provenance["base_revision_id"] == str(base)
    for key in ("provider", "model", "agent_run_ids", "cost_micros", "usage"):
        assert key not in second.provenance
    assert renv.messages(class_id) == [], "a refit is not a chat revision"


async def test_a_refit_with_nothing_to_move_is_stored_with_the_same_content(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    await refit(renv, class_id, base)
    first, second = renv.revisions(class_id)
    assert second.content == first.content
    assert bytes(second.content_hash) == bytes(first.content_hash)
    assert "beat_one" not in second.music


async def test_music_that_changed_since_the_base_ends_music_changed_and_stores_nothing(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, base = await renv.design()
    correct_all(renv, class_id)
    del fake_mowl.music.tracks[-1]  # the playlist changed since the base draft was made
    job_id = await refit(renv, class_id, base)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "music_changed")
    assert len(renv.revisions(class_id)) == 1
    assert renv.current(class_id) == base


async def test_admit_refuses_a_stale_base_a_foreign_one_a_busy_class_and_too_many_refits(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    _, other_base = await renv.design()
    foreign = await ask(renv, class_id, other_base)  # a revision of another class
    assert foreign.status_code == 409
    assert foreign.json()["detail"]["reason"] == "invalid_input"
    await renv.revise(class_id, base, "revise_good")
    stale = await ask(renv, class_id, base)
    assert (stale.status_code, stale.json()["detail"]["reason"]) == (409, "stale_base")
    current = renv.current(class_id)
    assert current is not None
    first = await ask(renv, class_id, current)
    assert first.status_code == 201
    busy = await ask(renv, class_id, current)
    assert busy.status_code == 409, busy.text  # one open job per class
    await run_refit(renv)
    newest = renv.current(class_id)
    assert newest is not None
    for n in range(MAX_REFITS):
        renv.sql(
            "INSERT INTO class_revisions (owner_id, class_id, number, kind, content, "
            "content_hash, music, provenance, parent_id) SELECT owner_id, id, 100 + :n, 'draft', "
            "'{}'::jsonb, decode(md5(CAST(:n AS text)) || md5(CAST(:n AS text)), 'hex'), "
            "'{}'::jsonb, CAST(:p AS jsonb), NULL FROM classes WHERE id = :c",
            n=n, c=class_id, p=json.dumps({"origin": "beat_one"}),
        )  # fmt: skip
    limited = await ask(renv, class_id, newest)
    assert limited.status_code == 409
    detail = limited.json()["detail"]
    assert detail["reason"] == "refit_limit"
    assert detail["limit"] == MAX_REFITS


async def test_a_browser_cannot_send_the_base_hash(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    body = {
        "kind": KIND,
        "input": {
            "class_id": str(class_id),
            "base_revision_id": str(base),
            "mowl_connection_id": str(renv.browser.connection_id),
            "base_revision_hash": "0" * 64,
        },
    }
    made = await renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert made.status_code == 422
