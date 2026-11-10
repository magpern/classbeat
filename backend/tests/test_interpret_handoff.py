"""``POST /api/classes/{id}/design-from-interpretation`` and ``GET .../interpretation`` (ADR 0029
section 5, MA1b-3), against Postgres as ``classbeat_app``, the fake MOWL and ``FakeProvider``.

Covered: the unchanged design job and its one class credit; idempotency (the key derived from the
interpret job); a refusal stored on the class with no job and no credit, answered again, and tried
again only with ``retry``; the call after a reopened tab; another user's class; the owner lock, and
two calls at the same time (two sessions held behind the owner lock) admitting exactly one job
with one credit, or both getting the same stored refusal; the first design's revision has no
``request_message_id`` while ``chat_full`` counts message seq 1."""

import asyncio
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from agentfake import provider
from classbeat.agent.brief import Brief
from classbeat.design import limits as design_limits
from classbeat.design import music_kind
from classbeat.design.interpretation_row import StoredInterpretation, handoff_key
from classbeat.jobs.credits import QuotaRefused, UnmeteredLedger
from classbeat.jobs.http import credit_ledger
from designtools import PLAYLIST
from interprettools import InterpretEnv
from jobapi import owner_lock_key
from jobtools import engine_for

pytestmark = pytest.mark.usefixtures("jenv")


class RefusingLedger(UnmeteredLedger):
    """The month's classes are used up: ``reserve`` refuses, after ``admit`` accepted."""

    async def reserve(self, db: Any, ctx: Any, job_id: UUID) -> None:
        raise QuotaRefused("classes", limit=3, used=3, plan="pro")


def empty_names(ienv: InterpretEnv, fake_mowl: Any) -> None:
    for token in fake_mowl.names:
        fake_mowl.names[token] = {}


async def wait_for_lock_waiters(ienv: InterpretEnv, n: int, seconds: float = 10) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while (
        ienv.sql(
            "SELECT count(*) FROM pg_stat_activity WHERE usename = 'classbeat_app' "
            "AND wait_event_type = 'Lock' AND wait_event = 'advisory'"
        ).scalar_one()
        < n
    ):
        assert loop.time() < deadline, f"fewer than {n} requests wait for the owner lock"
        await asyncio.sleep(0.02)


async def test_the_handoff_enqueues_the_unchanged_design_with_one_credit(
    ienv: InterpretEnv,
) -> None:
    class_id, job_id = await ienv.interpreted()
    stored = StoredInterpretation.model_validate(ienv.interpretation(class_id))
    before = ienv.credits()
    made = await ienv.handoff(class_id)
    assert made.status_code == 201, made.text
    body = made.json()
    assert (body["kind"], body["state"]) == ("design_draft", "queued")
    jobs = ienv.design_jobs(class_id)
    assert [(UUID(str(j.id)), j.credit_state, j.idempotency_key) for j in jobs] == [
        (UUID(body["job_id"]), "reserved", handoff_key(job_id))
    ]
    assert ienv.credits() == before + 1
    given = ienv.stored_input(jobs[0].id)
    # The stored brief, as a design job takes it (a cycling input brief leaves the sport out).
    assert Brief.model_validate(given["brief"]).stored() == stored.brief
    assert given["music"] == {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST}
    assert given["class_id"] == str(class_id)
    assert given["mowl_connection_id"] == str(ienv.renv.browser.connection_id)


async def test_a_second_call_and_a_reopened_tab_get_the_same_job(ienv: InterpretEnv) -> None:
    class_id, _ = await ienv.interpreted()
    first = await ienv.handoff(class_id)
    again = await ienv.handoff(class_id)
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    view = await ienv.renv.browser.get(f"/api/classes/{class_id}/interpretation")
    assert view.status_code == 200, view.text
    assert view.json()["design_job"] == first.json()
    assert ienv.credits() == 1
    assert len(ienv.design_jobs(class_id)) == 1


async def test_the_page_after_a_closed_tab_finds_no_design_job_and_calls_again(
    ienv: InterpretEnv,
) -> None:
    """The tab closed when the interpret job completed: the page reads the interpretation, sees no
    design job and no revision, and calls the hand-off itself."""
    class_id, job_id = await ienv.interpreted()
    view = (await ienv.renv.browser.get(f"/api/classes/{class_id}/interpretation")).json()
    assert view["design_job"] is None
    assert view["refusal"] is None
    assert view["interpret_job_id"] == str(job_id)
    assert view["sport"] == "cycling"
    assert view["music"] == "mowl_spotify_playlist"
    assert {i["path"] for i in view["items"]} >= {"length_min", "music"}
    assert (await ienv.handoff(class_id)).status_code == 201


async def test_the_whole_class_designs_and_its_first_revision_names_no_request(
    ienv: InterpretEnv,
) -> None:
    class_id, _ = await ienv.interpreted()
    ienv.models(provider("good_first_draft"))
    assert (await ienv.handoff(class_id)).status_code == 201
    await ienv.run("design_draft")
    [first] = ienv.renv.revisions(class_id)
    assert first.provenance.get("request_message_id") is None  # the request is in brief.text
    assert ienv.renv.current(class_id) == first.id
    assert [m.seq for m in ienv.renv.messages(class_id)] == [1]
    # ``chat_full`` counts seq 1: with one request allowed, the first revise is refused.
    settings = ienv.renv.denv.app.settings
    design_limits.configure(settings.model_copy(update={"chat_max_requests": 1}))
    asked = await ienv.renv.ask(class_id, first.id)
    assert asked.status_code == 409, asked.text
    detail = asked.json()["detail"]
    assert (detail["reason"], detail["count"], detail["limit"]) == ("chat_full", 1, 1)


async def test_a_refusal_is_stored_answered_again_and_retried_only_on_request(
    ienv: InterpretEnv, fake_mowl: Any
) -> None:
    empty_names(ienv, fake_mowl)
    music_kind.configure(None)  # the soundless switch off: ``design_draft`` refuses feature_off
    class_id, _ = await ienv.interpreted()
    assert ienv.interpretation(class_id)["music"] == {"source": "none"}
    before = ienv.counts()
    refused = await ienv.handoff(class_id)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "feature_off")
    assert ienv.counts() == before  # no job, no credit
    assert ienv.interpretation(class_id)["refusal"] == {"reason": "feature_off", "counts": {}}
    view = (await ienv.renv.browser.get(f"/api/classes/{class_id}/interpretation")).json()
    assert view["refusal"] == {"reason": "feature_off", "counts": {}}
    music_kind.configure(music_kind.SoundlessGate(enabled=True, fake_covers=True))
    again = await ienv.handoff(class_id)  # the stored refusal, though it would pass now
    assert again.json()["detail"]["reason"] == "feature_off"
    retried = await ienv.handoff(class_id, retry=True)
    assert retried.status_code == 201, retried.text
    assert ienv.interpretation(class_id)["refusal"] is None
    assert ienv.credits() == before[2] + 1
    music_kind.configure(None)


async def test_a_quota_refusal_rolls_back_the_create_and_is_stored(ienv: InterpretEnv) -> None:
    class_id, _ = await ienv.interpreted()
    app = ienv.renv.denv.app.app
    app.dependency_overrides[credit_ledger] = RefusingLedger
    try:
        before = ienv.counts()
        refused = await ienv.handoff(class_id)
        detail = refused.json()["detail"]
        assert (refused.status_code, detail["reason"], detail["quota"]) == (409, "quota", "classes")
        assert ienv.counts() == before  # the job row inserted before ``reserve`` is rolled back
        stored = ienv.interpretation(class_id)["refusal"]
        assert stored["reason"] == "quota"
        assert stored["counts"]["limit"] == 3
        assert (await ienv.handoff(class_id)).json() == refused.json()
    finally:
        app.dependency_overrides.pop(credit_ledger, None)


async def test_a_running_class_with_no_playlist_stores_needs_mowl_playlist(
    ienv: InterpretEnv,
) -> None:
    class_id, _ = await ienv.interpreted()
    ienv.sql(
        "UPDATE classes SET interpretation = jsonb_set(interpretation, '{music}', 'null') "
        "WHERE id = :c",
        c=class_id,
    )
    before = ienv.counts()
    refused = await ienv.handoff(class_id)
    assert refused.json()["detail"]["reason"] == "needs_mowl_playlist"
    assert ienv.interpretation(class_id)["refusal"]["reason"] == "needs_mowl_playlist"
    assert ienv.counts() == before


async def test_another_users_class_and_an_unknown_one_are_not_found(
    ienv: InterpretEnv, browsers: Any
) -> None:
    class_id, _ = await ienv.interpreted()
    other = await browsers()
    for who, cid in ((other, class_id), (ienv.renv.browser, UUID(int=7))):
        answer = await who.post(f"/api/classes/{cid}/design-from-interpretation")
        assert (answer.status_code, answer.json()["detail"]["reason"]) == (404, "not_found")
        assert (await who.get(f"/api/classes/{cid}/interpretation")).status_code == 404
    assert ienv.design_jobs(class_id) == []
    assert ienv.interpretation(class_id)["refusal"] is None


async def test_a_body_with_anything_but_retry_is_refused(ienv: InterpretEnv) -> None:
    class_id, _ = await ienv.interpreted()
    answer = await ienv.handoff(class_id, job_id="x")
    assert answer.status_code == 422
    assert ienv.design_jobs(class_id) == []


async def test_two_calls_at_once_admit_one_job_with_one_credit(
    ienv: InterpretEnv, database_url: str
) -> None:
    """Owner, 2026-11-10: while another transaction holds the user's owner lock both calls wait
    on it (two sessions of the app role), so they really run at the same time; once it is
    released exactly one design job and one credit exist, and both callers get its id and state."""
    class_id, _ = await ienv.interpreted()
    before = ienv.credits()
    with engine_for(database_url) as engine, engine.connect() as holder:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": owner_lock_key(ienv.owner)},
        )
        calls = [asyncio.create_task(ienv.handoff(class_id)) for _ in range(2)]
        await wait_for_lock_waiters(ienv, 2)
        assert not any(c.done() for c in calls)
        holder.commit()
        answers = await asyncio.gather(*calls)
    assert sorted(a.status_code for a in answers) == [200, 201]
    first, second = (a.json() for a in answers)
    assert first == second  # the same job id, kind and state
    assert first["state"] == "queued"
    assert len(ienv.design_jobs(class_id)) == 1
    assert ienv.credits() == before + 1


async def test_two_refused_calls_at_once_get_the_same_stored_refusal(
    ienv: InterpretEnv, database_url: str
) -> None:
    class_id, _ = await ienv.interpreted()
    app = ienv.renv.denv.app.app
    app.dependency_overrides[credit_ledger] = RefusingLedger
    try:
        before = ienv.counts()
        with engine_for(database_url) as engine, engine.connect() as holder:
            holder.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": owner_lock_key(ienv.owner)},
            )
            calls = [asyncio.create_task(ienv.handoff(class_id)) for _ in range(2)]
            await wait_for_lock_waiters(ienv, 2)
            holder.commit()
            answers = await asyncio.gather(*calls)
    finally:
        app.dependency_overrides.pop(credit_ledger, None)
    assert [a.status_code for a in answers] == [409, 409]
    assert answers[0].json() == answers[1].json()
    assert answers[0].json()["detail"]["reason"] == "quota"
    assert ienv.counts() == before  # no job, no credit
    assert ienv.interpretation(class_id)["refusal"]["reason"] == "quota"


async def test_the_handoff_waits_for_the_owner_lock_before_reading_the_refusal(
    ienv: InterpretEnv, database_url: str
) -> None:
    """The stored state is read again under the owner lock: a refusal written while the call
    waits on the lock (as another call's create would) is what the call answers."""
    class_id, _ = await ienv.interpreted()
    with engine_for(database_url) as engine, engine.connect() as holder:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": owner_lock_key(ienv.owner)},
        )
        call = asyncio.create_task(ienv.handoff(class_id))
        await wait_for_lock_waiters(ienv, 1)
        holder.execute(
            text(
                "UPDATE classes SET interpretation = jsonb_set(interpretation, '{refusal}', "
                '\'{"reason": "pool_busy", "counts": {}}\') WHERE id = :c'
            ),
            {"c": class_id},
        )
        holder.commit()
        answer = await call
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "pool_busy")
    assert ienv.design_jobs(class_id) == []
