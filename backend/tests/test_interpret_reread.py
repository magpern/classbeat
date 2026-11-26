"""``POST /api/classes/{id}/reread`` (#1536) through the jobs API and the worker side, against
Postgres as ``classbeat_app``, the fake MOWL and ``FakeProvider``: the answers become the next chat
message and one more ``interpret_request`` job on the same class, with no class credit; another
user's class is 404; a revision, an open job or a design job refuses it; two concurrent calls
admit one job; the new reading replaces the old one and the design that follows uses it. Never
the network, a real account or a model."""

import asyncio
import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest

from agentfake import provider
from classbeat import design
from classbeat.design import composer, music_kind
from classbeat.design.interpretation_row import handoff_key
from classbeat.jobs.credits import QuotaRefused
from interprettools import REQUEST, InterpretEnv, interpreter, refusing
from revisetools import ReviseEnv

pytestmark = pytest.mark.usefixtures("jenv")

SOURCES = [{"kind": "spotify_playlist", "id": "0123456789abcdefABCDEF"}]
ASKED: list[dict[str, Any]] = [
    {
        "topic": "length",
        "text": "How long should the class be?",
        "options": ["30 minutes", "45 minutes", "60 minutes"],
        "assumed_index": 0,
    },
    {
        "topic": "other",
        "text": "Any games on the bike?",
        "options": ["No games", "One game"],
        "assumed_index": 0,
    },
]
FIRST: dict[str, Any] = {
    "length_min": {"value": 30, "origin": "inferred"},
    "climbs": {"value": 1, "origin": "inferred"},
    "sprints": {"value": 2, "origin": "inferred"},
    "questions": ASKED,
}
SECOND: dict[str, Any] = {
    "length_min": {"value": 45, "origin": "explicit", "quote": "45 minutes"},
    "climbs": {"value": 1, "origin": "inferred"},
    "sprints": {"value": 2, "origin": "inferred"},
}
ANSWERS: dict[str, Any] = {
    "answers": [
        {"question_index": 0, "option_index": 1},
        {"question_index": 1, "text": "Only one race at the end"},
    ],
    "note": "Keep the climbs short.",
}
SAID = (
    "How long should the class be? Answer: 45 minutes "
    "Any games on the bike? Answer: Only one race at the end Also: Keep the climbs short."
)


@pytest.fixture(autouse=True)
def composer_on(renv: ReviseEnv) -> Iterator[None]:
    composer.configure(composer.ComposerGate(enabled=True))
    music_kind.configure(music_kind.SoundlessGate(enabled=False, fake_covers=True))
    renv.sql("DELETE FROM app_settings WHERE key = 'composer_flow'")
    renv.sql(
        "INSERT INTO app_settings (key, value) VALUES ('composer_flow', CAST(:v AS jsonb))",
        v=json.dumps("on"),
    )
    try:
        yield
    finally:
        composer.configure(None)
        music_kind.configure(None)
        renv.sql("DELETE FROM app_settings WHERE key = 'composer_flow'")


async def read(ienv: InterpretEnv, reading: dict[str, Any] = FIRST) -> tuple[UUID, UUID]:
    return await ienv.interpreted(reading, music_sources=SOURCES, music_override={"source": "pick"})


def path(class_id: UUID) -> str:
    return f"/api/classes/{class_id}/reread"


def messages(ienv: InterpretEnv, class_id: UUID) -> list[tuple[str, str]]:
    rows = ienv.sql(
        "SELECT role, body FROM class_messages WHERE class_id = :c ORDER BY seq", c=class_id
    ).all()
    return [(r.role, r.body) for r in rows]


async def test_the_answers_become_the_next_message_and_one_more_job_without_a_credit(
    ienv: InterpretEnv,
) -> None:
    class_id, first_job = await read(ienv)
    classes, jobs, credits = ienv.counts()
    made = await ienv.renv.browser.post(path(class_id), ANSWERS)
    assert made.status_code == 201, made.text
    body = made.json()
    assert (body["kind"], body["state"]) == ("interpret_request", "queued")
    assert ienv.counts() == (classes, jobs + 1, credits)  # the same class, no credit
    new_job = UUID(body["job_id"])
    given = ienv.stored_input(new_job)
    assert given["request"] == f"{REQUEST} {SAID}"
    assert (given["class_id"], given["reread_of"]) == (str(class_id), str(first_job))
    assert given["music_sources"] == SOURCES  # the first request's browser fields are kept
    assert messages(ienv, class_id) == [("user", REQUEST), ("user", SAID)]
    credit_state = ienv.sql("SELECT credit_state FROM jobs WHERE id = :j", j=new_job).scalar_one()
    assert credit_state == "none"


async def test_the_new_reading_replaces_the_old_one_and_the_design_uses_it(
    ienv: InterpretEnv,
) -> None:
    class_id, first_job = await read(ienv)
    assert ienv.interpretation(class_id)["brief"]["length_min"] == 30
    made = await ienv.renv.browser.post(path(class_id), ANSWERS)
    new_job = UUID(made.json()["job_id"])
    ienv.models(interpreter(SECOND))
    await ienv.run("interpret_request")
    assert ienv.renv.denv.ops.row(new_job).state == "completed"
    stored = ienv.interpretation(class_id)
    assert stored["job_id"] == str(new_job)
    assert stored["brief"]["length_min"] == 45
    assert stored["refusal"] is None
    assert "questions" not in stored  # the new reading asked nothing
    assert ienv.counts()[0] == 1  # still one class
    handed = await ienv.handoff(class_id)
    assert handed.status_code == 201, handed.text
    designed = ienv.sql(
        "SELECT idempotency_key, input FROM jobs WHERE id = :j", j=UUID(handed.json()["job_id"])
    ).one()
    assert designed.idempotency_key == handoff_key(new_job) != handoff_key(first_job)
    given = designed.input if isinstance(designed.input, dict) else json.loads(designed.input)
    assert given["brief"]["length_min"] == 45
    assert ienv.credits() == 1  # the design's one credit, never the re-read's


async def test_another_users_class_and_an_unknown_one_are_404(
    ienv: InterpretEnv, browsers: Any
) -> None:
    class_id, _ = await read(ienv)
    other = await browsers()
    before = ienv.counts()
    for who, cid in ((other, class_id), (ienv.renv.browser, UUID(int=7))):
        answer = await who.post(path(cid), ANSWERS)
        assert (answer.status_code, answer.json()["detail"]["reason"]) == (404, "not_found")
    assert ienv.counts() == before


@pytest.mark.parametrize(
    ("body", "status", "reason"),
    [
        ({"answers": [{"question_index": 2, "option_index": 0}]}, 422, "invalid_answer"),
        ({"answers": [{"question_index": 0, "option_index": 3}]}, 422, "invalid_answer"),
        (
            {"answers": [{"question_index": 0, "option_index": 0}] * 2},
            422,
            "invalid_answer",
        ),
        ({}, 422, "invalid_answer"),  # nothing answered
        (
            {"answers": [{"question_index": 0, "option_index": 0, "text": "x"}]},
            422,
            "invalid_input",
        ),
        ({"answers": [{"question_index": 0}]}, 422, "invalid_input"),
        ({"answers": [{"question_index": 0, "text": "x" * 201}]}, 422, "invalid_input"),
        ({"note": "n" * 501}, 422, "invalid_input"),
        ({"answers": [{"question_index": 0, "option_index": 0}], "extra": 1}, 422, "invalid_input"),
    ],
)
async def test_answers_that_name_no_question_or_option_are_refused(
    ienv: InterpretEnv, body: dict[str, Any], status: int, reason: str
) -> None:
    class_id, _ = await read(ienv)
    before = ienv.counts()
    refused = await ienv.renv.browser.post(path(class_id), body)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (status, reason)
    assert ienv.counts() == before


async def test_a_request_over_its_limit_is_refused_too_large(ienv: InterpretEnv) -> None:
    ienv.models(interpreter(FIRST))
    long_request = "A 30 minute class with one climb. " + "a" * 1900
    made = await ienv.start(long_request, music_sources=SOURCES, music_override={"source": "pick"})
    assert made.status_code == 201, made.text
    await ienv.run("interpret_request")
    class_id = UUID(made.json()["class_id"])
    before = ienv.counts()
    refused = await ienv.renv.browser.post(path(class_id), ANSWERS)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (422, "too_large")
    assert ienv.counts() == before


async def test_a_class_with_no_stored_reading_is_not_ready(ienv: InterpretEnv) -> None:
    ienv.models(interpreter(FIRST))
    made = await ienv.start(music_sources=SOURCES, music_override={"source": "pick"})
    class_id = UUID(made.json()["class_id"])  # the interpret job has not run
    refused = await ienv.renv.browser.post(path(class_id), ANSWERS)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "not_ready")


async def test_a_revision_refuses_it_without_a_job(ienv: InterpretEnv) -> None:
    composer.configure(None)  # the classic route: the recorded draft script fits it
    class_id, _ = await ienv.interpreted()
    stored = ienv.interpretation(class_id)
    stored["questions"] = ASKED  # a reading with questions, planted: the classic one has none
    ienv.sql(
        "UPDATE classes SET interpretation = CAST(:v AS jsonb) WHERE id = :c",
        v=json.dumps(stored),
        c=class_id,
    )
    handed = await ienv.handoff(class_id)
    assert handed.status_code == 201, handed.text
    before = ienv.counts()
    refused = await ienv.renv.browser.post(path(class_id), ANSWERS)  # a design job, queued
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "class_busy")
    ienv.models(provider("good_first_draft"))
    await ienv.run(design.KIND)
    assert ienv.renv.current(class_id) is not None  # a revision, no open job
    refused = await ienv.renv.browser.post(path(class_id), ANSWERS)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "class_designed")
    assert ienv.counts() == before


async def test_a_failed_design_job_still_refuses_it(ienv: InterpretEnv) -> None:
    class_id, _ = await read(ienv)
    handed = await ienv.handoff(class_id)
    ienv.sql(
        "UPDATE jobs SET state = 'cancelled', finished_at = now() WHERE id = :j",
        j=UUID(handed.json()["job_id"]),
    )
    before = ienv.counts()
    refused = await ienv.renv.browser.post(path(class_id), ANSWERS)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "class_designed")
    assert ienv.counts() == before


async def test_an_open_job_refuses_it_and_a_repeat_gets_the_same_job(ienv: InterpretEnv) -> None:
    class_id, _ = await read(ienv)
    first = await ienv.renv.browser.post(path(class_id), ANSWERS)
    assert first.status_code == 201, first.text
    jobs = ienv.counts()[1]
    again = await ienv.renv.browser.post(path(class_id), ANSWERS)
    assert (again.status_code, again.json()) == (200, first.json())
    other = await ienv.renv.browser.post(
        path(class_id), {"answers": [{"question_index": 0, "option_index": 2}]}
    )
    assert other.status_code == 409, other.text  # other answers while one is open
    assert ienv.counts()[1] == jobs
    assert len(messages(ienv, class_id)) == 2  # one message for the one job


async def test_two_concurrent_calls_admit_one_job(ienv: InterpretEnv) -> None:
    class_id, _ = await read(ienv)
    classes, jobs, credits = ienv.counts()
    answers = await asyncio.gather(
        *(ienv.renv.browser.post(path(class_id), ANSWERS) for _ in range(2))
    )
    assert sorted(a.status_code for a in answers) == [200, 201]
    assert answers[0].json()["job_id"] == answers[1].json()["job_id"]
    assert ienv.counts() == (classes, jobs + 1, credits)
    assert len(messages(ienv, class_id)) == 2


async def test_a_failed_reread_keeps_the_old_reading_and_can_be_asked_again(
    ienv: InterpretEnv,
) -> None:
    class_id, first_job = await read(ienv)
    made = await ienv.renv.browser.post(path(class_id), ANSWERS)
    ienv.models(refusing())
    await ienv.run("interpret_request")
    assert ienv.renv.denv.ops.row(UUID(made.json()["job_id"])).state == "failed"
    assert ienv.interpretation(class_id)["job_id"] == str(first_job)  # the old reading stays
    assert ienv.counts()[0] == 1  # the class is not discarded
    again = await ienv.renv.browser.post(path(class_id), ANSWERS)
    assert again.status_code == 201, again.text
    assert again.json()["job_id"] != made.json()["job_id"]


async def test_a_cancelled_reread_keeps_the_class_and_its_reading(ienv: InterpretEnv) -> None:
    class_id, first_job = await read(ienv)
    made = await ienv.renv.browser.post(path(class_id), ANSWERS)
    cancelled = await ienv.renv.browser.post(f"/api/jobs/{made.json()['job_id']}/cancel", None)
    assert cancelled.status_code == 200, cancelled.text
    assert ienv.counts()[0] == 1
    assert ienv.interpretation(class_id)["job_id"] == str(first_job)


async def test_a_reread_counts_toward_the_per_user_limit(ienv: InterpretEnv) -> None:
    class_id, _ = await read(ienv)
    for _ in range(4):  # queued interprets of other classes fill the user's jobs at once
        await ienv.start(music_sources=SOURCES, music_override={"source": "pick"})
    refused = await ienv.renv.browser.post(path(class_id), ANSWERS)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "busy")


async def test_the_jobs_api_refuses_the_server_fields_of_a_reread(ienv: InterpretEnv) -> None:
    class_id, first_job = await read(ienv)
    ienv.models(interpreter(FIRST))
    body = {
        "request": REQUEST,
        "mowl_connection_id": str(ienv.renv.browser.connection_id),
        "class_id": str(class_id),
        "reread_of": str(first_job),
        "answers": "x",
    }
    for extra in ({}, {"title": "t"}):
        sent = {"kind": "interpret_request", "input": body | extra}
        refused = await ienv.renv.browser.post(
            "/api/jobs", sent, {"Idempotency-Key": f"reread-api-{len(extra)}-0001"}
        )
        assert refused.status_code == 422, refused.text
    plain = {
        "request": REQUEST,
        "class_id": str(class_id),
        "mowl_connection_id": body["mowl_connection_id"],
    }
    refused = await ienv.renv.browser.post(
        "/api/jobs",
        {"kind": "interpret_request", "input": plain},
        {"Idempotency-Key": "reread-api-plain-0001"},
    )
    assert refused.status_code == 422, refused.text  # an interpretation still starts a new class


async def test_the_cost_cap_refuses_it(ienv: InterpretEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    class_id, _ = await read(ienv)

    async def capped(db: Any, ctx: Any, limits: Any = None) -> None:
        raise QuotaRefused("cost", limit=1, used=1, plan="pro")

    monkeypatch.setattr("classbeat.quota.check_cost", capped)
    before = ienv.counts()
    refused = await ienv.renv.browser.post(path(class_id), ANSWERS)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "quota")
    assert ienv.counts() == before
