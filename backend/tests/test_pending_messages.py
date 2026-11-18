"""Pending chat messages (ADR 0038 section 8, #1186): the state table (pure), the storage and its
idempotent send, and the idempotent submit by message id through the jobs API, against Postgres as
``classbeat_app`` with the fake MOWL and ``FakeProvider``. A refused submit is ``failed`` with its
code and never stays pending; two submits at once make one job. Never the network, a real account
or a model."""

import asyncio
import uuid
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from classbeat.classes import pending_policy
from classbeat.classes.pending import MAX_STORED
from classbeat.classes.pending_policy import ClassState
from classbeat.classes.pending_submit import submit_key
from classbeat.design import limits as design_limits
from classbeat.jobs.credits import QuotaRefused
from jobapi import owner_lock_key
from jobtools import engine_for
from sketchtools import SketchEnv, composer_on
from test_interpret_handoff import wait_for_lock_waiters

pytestmark = pytest.mark.usefixtures("jenv")

BODY = "A longer warm-up, please"


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (ClassState(True, False, "draft", "sketch"), "busy"),
        (ClassState(True, True, "draft", "sketch"), "busy"),
        (ClassState(False, True, "draft", "sketch"), "change"),
        (ClassState(False, True, "draft", "design_with_songs"), "change"),
        (ClassState(False, False, "draft", "sketch"), "sketch_revise"),
        (ClassState(False, False, "draft", "design_with_songs"), "revise"),
        (ClassState(False, False, "draft", "design_draft"), "revise"),
        (ClassState(False, False, "draft", None), "revise"),
        (ClassState(False, False, None, None), "not_changeable"),
        (ClassState(False, False, "song_list", "design_with_songs"), "not_changeable"),
    ],
)
def test_the_state_of_the_class_decides(state: ClassState, expected: str) -> None:
    assert pending_policy.action(state) == expected


# --- the routes -------------------------------------------------------------------------------


async def send(
    skenv: SketchEnv, class_id: UUID, message_id: UUID, body: str = BODY
) -> httpx.Response:
    payload = {"message_id": str(message_id), "body": body}
    return await skenv.renv.browser.post(f"/api/classes/{class_id}/pending-messages", payload)


async def submit(skenv: SketchEnv, class_id: UUID, message_id: UUID) -> httpx.Response:
    path = f"/api/classes/{class_id}/pending-messages/{message_id}/submit"
    return await skenv.renv.browser.post(path)


def stored(skenv: SketchEnv, message_id: UUID, class_id: UUID | None = None) -> Any:
    where = "message_id = :m" + ("" if class_id is None else " AND class_id = :c")
    return skenv.sql(
        f"SELECT status, reason, job_id FROM class_pending_messages WHERE {where}",  # noqa: S608
        m=message_id,
        c=class_id,
    ).one()


def revise_jobs(skenv: SketchEnv, class_id: UUID) -> list[Any]:
    return skenv.sql(
        "SELECT id, idempotency_key, input FROM jobs WHERE class_id = :c AND kind = 'revise_draft'",
        c=class_id,
    ).all()


async def test_a_message_is_stored_once_by_its_id(skenv: SketchEnv) -> None:
    class_id, _ = await skenv.sketched()
    message = uuid.uuid4()
    first = await send(skenv, class_id, message)
    again = await send(skenv, class_id, message)
    assert (first.status_code, again.status_code) == (201, 200), first.text
    assert first.json() == again.json()
    assert (first.json()["status"], first.json()["body"]) == ("pending", BODY)
    other = await send(skenv, class_id, message, "Something else")
    assert (other.status_code, other.json()["detail"]["reason"]) == (409, "idempotency_mismatch")
    second = await send(skenv, class_id, uuid.uuid4(), "And shorter")
    assert (second.status_code, second.json()["detail"]["reason"]) == (409, "message_pending")
    listed = await skenv.renv.browser.get(f"/api/classes/{class_id}/pending-messages")
    assert [m["message_id"] for m in listed.json()["messages"]] == [str(message)]


async def test_with_the_flag_off_nothing_is_stored(skenv: SketchEnv) -> None:
    class_id, _ = await skenv.sketched()
    composer_on(skenv.renv, False)
    refused = await send(skenv, class_id, uuid.uuid4())
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "feature_off")
    assert skenv.sql("SELECT count(*) FROM class_pending_messages").scalar_one() == 0
    unknown = await send(skenv, uuid.uuid4(), uuid.uuid4())
    assert unknown.status_code == 404


async def test_on_a_sketch_it_becomes_a_sketch_revise_once(skenv: SketchEnv) -> None:
    class_id, sketch = await skenv.sketched()
    message = uuid.uuid4()
    assert (await send(skenv, class_id, message)).status_code == 201
    made = await submit(skenv, class_id, message)
    assert made.status_code == 201, made.text
    again = await submit(skenv, class_id, message)
    assert again.status_code == 200
    assert made.json()["job_id"] == again.json()["job_id"]
    [job] = revise_jobs(skenv, class_id)
    assert job.idempotency_key == submit_key(class_id, message)
    assert (job.input["base_revision_id"], job.input["request"]) == (str(sketch), BODY)
    assert job.input["sketch_base"] is True
    assert tuple(stored(skenv, message)) == ("submitted", None, job.id)
    skenv.renv.use("soundless_revise_good")
    await skenv.renv.run("revise_draft")
    new = skenv.renv.current(class_id)
    assert new is not None
    assert new != sketch
    assert skenv.revision(new).provenance["origin"] == "sketch"
    assert (await submit(skenv, class_id, message)).json()["status"] == "submitted"


async def test_on_a_draft_it_becomes_an_ordinary_revise(skenv: SketchEnv) -> None:
    class_id = await skenv.interpreted(music=False)  # "No music": its first design is the class
    draft = await skenv.designed(class_id)
    message = uuid.uuid4()
    await send(skenv, class_id, message)
    made = await submit(skenv, class_id, message)
    assert made.status_code == 201, made.text
    [job] = revise_jobs(skenv, class_id)
    assert job.input["base_revision_id"] == str(draft)
    assert "sketch_base" not in job.input


async def test_while_a_job_is_queued_it_stays_pending(skenv: SketchEnv) -> None:
    class_id = await skenv.interpreted()
    assert (await skenv.ienv.handoff(class_id)).status_code == 201  # the sketch job is queued
    message = uuid.uuid4()
    await send(skenv, class_id, message)
    busy = await submit(skenv, class_id, message)
    assert (busy.status_code, busy.json()["detail"]["reason"]) == (409, "class_busy")
    assert tuple(stored(skenv, message)) == ("pending", None, None)
    skenv.renv.use("soundless_first_draft")
    await skenv.renv.run("design_draft")  # the sketch is in: now it is submitted
    assert (await submit(skenv, class_id, message)).status_code == 201


async def test_with_a_song_list_waiting_it_fails_not_changeable_for_now(skenv: SketchEnv) -> None:
    """A change replaces the waiting job (ADR 0038 section 6); it has no job kind before #1188
    and #1189, so the message is refused and never stays pending."""
    class_id, _ = await skenv.sketched()
    job = skenv.sql(
        "SELECT id FROM jobs WHERE class_id = :c AND kind = 'design_draft'", c=class_id
    ).scalar_one()
    skenv.sql(  # stands in for a song list waiting for approval (the kinds of #1188)
        "UPDATE jobs SET state = 'waiting_for_approval', approval_hash = sha256('list'), "
        "expires_at = now() + interval '1 hour', finished_at = NULL WHERE id = :j",
        j=job,
    )
    message = uuid.uuid4()
    await send(skenv, class_id, message)
    refused = await submit(skenv, class_id, message)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "not_changeable")
    assert tuple(stored(skenv, message)) == ("failed", "not_changeable", None)
    assert revise_jobs(skenv, class_id) == []


async def test_with_no_current_revision_it_fails_not_changeable(skenv: SketchEnv) -> None:
    class_id = await skenv.interpreted()  # read, never handed off: no revision, no open job
    message = uuid.uuid4()
    await send(skenv, class_id, message)
    refused = await submit(skenv, class_id, message)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "not_changeable")
    assert stored(skenv, message).status == "failed"
    answered = await submit(skenv, class_id, message)  # answered as it is, nothing tried again
    assert (answered.status_code, answered.json()["reason"]) == (200, "not_changeable")


@pytest.mark.parametrize("limit", ["revisions_per_class", "chat_max_requests"])
async def test_a_refused_revise_marks_it_failed_with_the_reason(
    skenv: SketchEnv, limit: str
) -> None:
    class_id, sketch = await skenv.sketched()
    settings = skenv.renv.denv.app.settings
    design_limits.configure(settings.model_copy(update={limit: 1}))
    try:
        if limit == "revisions_per_class":  # one sketch revise uses the limit
            await skenv.renv.revise(class_id, sketch, "soundless_revise_good", "Easier")
        message = uuid.uuid4()
        await send(skenv, class_id, message)
        refused = await submit(skenv, class_id, message)
    finally:
        design_limits.configure(settings)
    reason = "revision_limit" if limit == "revisions_per_class" else "chat_full"
    detail = refused.json()["detail"]
    assert (refused.status_code, detail["reason"], detail["limit"]) == (409, reason, 1)
    assert tuple(stored(skenv, message)) == ("failed", reason, None)
    # The next message can be stored: a failed one no longer holds the class's pending slot.
    assert (await send(skenv, class_id, uuid.uuid4(), "Try again")).status_code == 201


async def test_a_revise_over_the_cost_cap_marks_it_failed_with_quota(
    skenv: SketchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    class_id, _ = await skenv.sketched()

    async def capped(db: Any, ctx: Any, limits: Any = None) -> None:
        raise QuotaRefused("cost", limit=1, used=1, plan="pro")

    monkeypatch.setattr("classbeat.quota.check_cost", capped)
    message = uuid.uuid4()
    await send(skenv, class_id, message)
    refused = await submit(skenv, class_id, message)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "quota")
    assert tuple(stored(skenv, message)) == ("failed", "quota", None)
    assert revise_jobs(skenv, class_id) == []


async def test_two_submits_at_once_make_one_job(skenv: SketchEnv, database_url: str) -> None:
    class_id, _ = await skenv.sketched()
    message = uuid.uuid4()
    await send(skenv, class_id, message)
    with engine_for(database_url) as engine, engine.connect() as holder:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": owner_lock_key(skenv.renv.owner)},
        )
        calls = [asyncio.create_task(submit(skenv, class_id, message)) for _ in range(2)]
        await wait_for_lock_waiters(skenv.ienv, 2)
        holder.commit()
        answers = await asyncio.gather(*calls)
    assert sorted(a.status_code for a in answers) == [200, 201], [a.text for a in answers]
    assert answers[0].json()["job_id"] == answers[1].json()["job_id"]
    [job] = revise_jobs(skenv, class_id)
    assert tuple(stored(skenv, message)) == ("submitted", None, job.id)


async def test_the_same_message_id_in_two_classes_makes_a_job_for_each(skenv: SketchEnv) -> None:
    """A message id is unique only within its class: the job's key and its lookup name both."""
    first, _ = await skenv.sketched()
    second, _ = await skenv.sketched()
    message = uuid.uuid4()
    for class_id in (first, second):
        await send(skenv, class_id, message)
        made = await submit(skenv, class_id, message)
        assert made.status_code == 201, made.text
    [one], [two] = revise_jobs(skenv, first), revise_jobs(skenv, second)
    assert one.id != two.id
    assert one.idempotency_key != two.idempotency_key
    assert tuple(stored(skenv, message, first)) == ("submitted", None, one.id)
    assert tuple(stored(skenv, message, second)) == ("submitted", None, two.id)


async def test_a_class_holds_at_most_max_stored_messages(skenv: SketchEnv) -> None:
    """Store, a refused submit and store again cannot grow the table without bound."""
    class_id, _ = await skenv.sketched()
    owner = skenv.renv.owner
    for _ in range(MAX_STORED):
        skenv.sql(
            "INSERT INTO class_pending_messages (owner_id, class_id, message_id, body, status, "
            "reason) VALUES (:o, :c, gen_random_uuid(), 'earlier', 'failed', 'not_changeable')",
            o=owner,
            c=class_id,
        )
    full = await send(skenv, class_id, uuid.uuid4())
    detail = full.json()["detail"]
    assert (full.status_code, detail["reason"]) == (409, "pending_full")
    assert (detail["limit"], detail["count"]) == (MAX_STORED, MAX_STORED)
    count = "SELECT count(*) FROM class_pending_messages WHERE class_id = :c"
    assert skenv.sql(count, c=class_id).scalar_one() == MAX_STORED
    listed = await skenv.renv.browser.get(f"/api/classes/{class_id}/pending-messages")
    assert len(listed.json()["messages"]) == MAX_STORED  # the list answers every one


async def test_another_user_reaches_nothing_with_the_flag_on(
    skenv: SketchEnv, browsers: Any
) -> None:
    """With ``composer_flow`` on, another user's class or message is ``not_found`` on all three
    routes, and nothing of the owner's changes (``test_m6_security_routes`` runs them flag off)."""
    class_id, _ = await skenv.sketched()
    message = uuid.uuid4()
    await send(skenv, class_id, message)
    stranger = await browsers()
    base = f"/api/classes/{class_id}/pending-messages"
    payload = {"message_id": str(uuid.uuid4()), "body": BODY}
    answers = [
        await stranger.get(base),
        await stranger.post(base, payload),
        await stranger.post(base, {"message_id": str(message), "body": BODY}),
        await stranger.post(f"{base}/{message}/submit"),
    ]
    for answer in answers:
        assert (answer.status_code, answer.json()["detail"]["reason"]) == (404, "not_found")
    rows = "SELECT count(*) FROM class_pending_messages WHERE class_id = :c"
    assert skenv.sql(rows, c=class_id).scalar_one() == 1
    assert tuple(stored(skenv, message)) == ("pending", None, None)
    assert revise_jobs(skenv, class_id) == []
