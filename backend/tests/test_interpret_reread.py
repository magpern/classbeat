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
from classbeat.classes import reread as reread_module
from classbeat.design import composer, interpret_admit, interpretation_row, limits, music_kind
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


def said_to(reading: UUID, **over: Any) -> dict[str, Any]:
    """``ANSWERS`` for the reading (its interpret job) the instructor answered."""
    return {**ANSWERS, "interpret_job_id": str(reading), **over}


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
    made = await ienv.renv.browser.post(path(class_id), said_to(first_job))
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
    made = await ienv.renv.browser.post(path(class_id), said_to(first_job))
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
    class_id, first_job = await read(ienv)
    other = await browsers()
    before = ienv.counts()
    for who, cid in ((other, class_id), (ienv.renv.browser, UUID(int=7))):
        answer = await who.post(path(cid), {**ANSWERS, "interpret_job_id": str(first_job)})
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
    class_id, first_job = await read(ienv)
    before = ienv.counts()
    refused = await ienv.renv.browser.post(
        path(class_id), {**body, "interpret_job_id": str(first_job)}
    )
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
    refused = await ienv.renv.browser.post(path(class_id), said_to(UUID(made.json()["id"])))
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (422, "too_large")
    assert ienv.counts() == before


async def test_a_class_with_no_stored_reading_is_not_ready(ienv: InterpretEnv) -> None:
    ienv.models(interpreter(FIRST))
    made = await ienv.start(music_sources=SOURCES, music_override={"source": "pick"})
    class_id = UUID(made.json()["class_id"])  # the interpret job has not run
    refused = await ienv.renv.browser.post(path(class_id), said_to(UUID(made.json()["id"])))
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "not_ready")


async def test_a_revision_refuses_it_without_a_job(ienv: InterpretEnv) -> None:
    composer.configure(None)  # the classic route: the recorded draft script fits it
    class_id, first_job = await ienv.interpreted()
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
    refused = await ienv.renv.browser.post(
        path(class_id), said_to(first_job)
    )  # a design job, queued
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "class_busy")
    ienv.models(provider("good_first_draft"))
    await ienv.run(design.KIND)
    assert ienv.renv.current(class_id) is not None  # a revision, no open job
    refused = await ienv.renv.browser.post(path(class_id), said_to(first_job))
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "class_designed")
    assert ienv.counts() == before


async def test_a_failed_design_job_still_refuses_it(ienv: InterpretEnv) -> None:
    class_id, first_job = await read(ienv)
    handed = await ienv.handoff(class_id)
    ienv.sql(
        "UPDATE jobs SET state = 'cancelled', finished_at = now() WHERE id = :j",
        j=UUID(handed.json()["job_id"]),
    )
    before = ienv.counts()
    refused = await ienv.renv.browser.post(path(class_id), said_to(first_job))
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "class_designed")
    assert ienv.counts() == before


async def test_an_open_job_refuses_it_and_a_repeat_gets_the_same_job(ienv: InterpretEnv) -> None:
    class_id, first_job = await read(ienv)
    first = await ienv.renv.browser.post(path(class_id), said_to(first_job))
    assert first.status_code == 201, first.text
    jobs = ienv.counts()[1]
    again = await ienv.renv.browser.post(path(class_id), said_to(first_job))
    assert (again.status_code, again.json()) == (200, first.json())
    other = await ienv.renv.browser.post(
        path(class_id),
        {"interpret_job_id": str(first_job), "answers": [{"question_index": 0, "option_index": 2}]},
    )
    assert other.status_code == 409, other.text  # other answers while one is open
    assert ienv.counts()[1] == jobs
    assert len(messages(ienv, class_id)) == 2  # one message for the one job


async def test_two_concurrent_calls_admit_one_job(ienv: InterpretEnv) -> None:
    class_id, first_job = await read(ienv)
    classes, jobs, credits = ienv.counts()
    answers = await asyncio.gather(
        *(ienv.renv.browser.post(path(class_id), said_to(first_job)) for _ in range(2))
    )
    assert sorted(a.status_code for a in answers) == [200, 201]
    assert answers[0].json()["job_id"] == answers[1].json()["job_id"]
    assert ienv.counts() == (classes, jobs + 1, credits)
    assert len(messages(ienv, class_id)) == 2


async def test_a_failed_reread_keeps_the_old_reading_and_can_be_asked_again(
    ienv: InterpretEnv,
) -> None:
    class_id, first_job = await read(ienv)
    made = await ienv.renv.browser.post(path(class_id), said_to(first_job))
    ienv.models(refusing())
    await ienv.run("interpret_request")
    assert ienv.renv.denv.ops.row(UUID(made.json()["job_id"])).state == "failed"
    assert ienv.interpretation(class_id)["job_id"] == str(first_job)  # the old reading stays
    assert ienv.counts()[0] == 1  # the class is not discarded
    again = await ienv.renv.browser.post(path(class_id), said_to(first_job))
    assert again.status_code == 201, again.text
    assert again.json()["job_id"] != made.json()["job_id"]


async def test_a_cancelled_reread_keeps_the_class_and_its_reading(ienv: InterpretEnv) -> None:
    class_id, first_job = await read(ienv)
    made = await ienv.renv.browser.post(path(class_id), said_to(first_job))
    cancelled = await ienv.renv.browser.post(f"/api/jobs/{made.json()['job_id']}/cancel", None)
    assert cancelled.status_code == 200, cancelled.text
    assert ienv.counts()[0] == 1
    assert ienv.interpretation(class_id)["job_id"] == str(first_job)


async def test_a_reread_counts_toward_the_per_user_limit(ienv: InterpretEnv) -> None:
    class_id, first_job = await read(ienv)
    for _ in range(4):  # queued interprets of other classes fill the user's jobs at once
        await ienv.start(music_sources=SOURCES, music_override={"source": "pick"})
    refused = await ienv.renv.browser.post(path(class_id), said_to(first_job))
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
    class_id, first_job = await read(ienv)

    async def capped(db: Any, ctx: Any, limits: Any = None) -> None:
        raise QuotaRefused("cost", limit=1, used=1, plan="pro")

    monkeypatch.setattr("classbeat.quota.check_cost", capped)
    before = ienv.counts()
    refused = await ienv.renv.browser.post(path(class_id), said_to(first_job))
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "quota")
    assert ienv.counts() == before


async def second_reading(ienv: InterpretEnv, class_id: UUID, first_job: UUID) -> UUID:
    """A completed re-read: the class's reading is now another job's (and asks nothing)."""
    made = await ienv.renv.browser.post(path(class_id), said_to(first_job))
    assert made.status_code == 201, made.text
    ienv.models(interpreter(SECOND))
    await ienv.run("interpret_request")
    return UUID(made.json()["job_id"])


async def test_answers_to_an_old_reading_are_refused_with_no_job_and_no_message(
    ienv: InterpretEnv,
) -> None:
    class_id, first_job = await read(ienv)
    second_job = await second_reading(ienv, class_id, first_job)
    before, said = ienv.counts(), messages(ienv, class_id)
    stale = await ienv.renv.browser.post(path(class_id), said_to(first_job))  # a stale tab
    assert (stale.status_code, stale.json()["detail"]["reason"]) == (409, "not_ready")
    assert (ienv.counts(), messages(ienv, class_id)) == (before, said)
    assert ienv.interpretation(class_id)["job_id"] == str(second_job)


async def test_admit_refuses_a_reading_that_was_replaced_after_the_route_read_it(
    ienv: InterpretEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route's own comparison passes (its read is the old reading); only admit, under the owner
    lock, sees the stored one."""
    class_id, first_job = await read(ienv)
    old = interpretation_row.StoredInterpretation.model_validate(ienv.interpretation(class_id))
    await second_reading(ienv, class_id, first_job)
    real = interpretation_row.read

    async def old_read(db: Any, owner: Any, cid: UUID) -> Any:
        return old if cid == class_id else await real(db, owner, cid)

    monkeypatch.setattr(
        reread_module, "interpretation_row", type("Stale", (), {"read": staticmethod(old_read)})
    )
    # A new key: the old one belongs to the re-read that completed, and its repeat is that job.
    monkeypatch.setattr(reread_module, "reread_key", lambda job, ended: f"stale-{job.hex}-{ended}")
    before, said = ienv.counts(), messages(ienv, class_id)
    refused = await ienv.renv.browser.post(path(class_id), said_to(first_job))
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "not_ready"
    assert (ienv.counts(), messages(ienv, class_id)) == (before, said)


async def test_the_hand_off_designs_only_the_reading_it_names(ienv: InterpretEnv) -> None:
    class_id, first_job = await read(ienv)
    second_job = await second_reading(ienv, class_id, first_job)
    before = ienv.counts()
    stale = await ienv.handoff(class_id, interpret_job_id=str(first_job))
    assert (stale.status_code, stale.json()["detail"]["reason"]) == (409, "not_ready")
    assert ienv.counts() == before  # no design job
    wrong = await ienv.handoff(class_id, interpret_job_id="not-an-id")
    assert wrong.status_code == 422, wrong.text
    named = await ienv.handoff(class_id, interpret_job_id=str(second_job))
    assert named.status_code == 201, named.text
    again = await ienv.handoff(class_id)  # an older client names none: the same hand-off
    assert (again.status_code, again.json()["job_id"]) == (200, named.json()["job_id"])


async def test_a_reread_is_refused_when_the_chat_holds_its_most_requests(
    ienv: InterpretEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    class_id, first_job = await read(ienv)  # one user message so far
    monkeypatch.setattr(limits, "_limits", limits.ChatLimits(max_requests=1))
    before = ienv.counts()
    refused = await ienv.renv.browser.post(path(class_id), said_to(first_job))
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        {"reason": "chat_full", "limit": 1, "count": 1},
    )
    assert ienv.counts() == before
    monkeypatch.setattr(limits, "_limits", limits.ChatLimits(max_requests=2))
    allowed = await ienv.renv.browser.post(path(class_id), said_to(first_job))
    assert allowed.status_code == 201, allowed.text


MUSIC_Q: list[dict[str, Any]] = [
    {
        "topic": "music",
        "text": "What about the music?",
        "options": ["Pop hits", "Use one of my playlists", "No music"],
        "no_music_index": 2,  # no assumed answer: the instructor must answer it
    },
    {
        "topic": "length",
        "text": "How long?",
        "options": ["No music", "45 minutes"],  # the same words, but not the marked option
        "assumed_index": 1,
    },
]
MUSIC_READING: dict[str, Any] = {
    "length_min": {"value": 45, "origin": "inferred"},
    "questions": MUSIC_Q,
}


def soundless(on: bool) -> None:
    music_kind.configure(music_kind.SoundlessGate(enabled=on, fake_covers=True))


def option(reading: UUID, question: int, index: int) -> dict[str, Any]:
    return {
        "interpret_job_id": str(reading),
        "answers": [{"question_index": question, "option_index": index}],
    }


async def test_the_no_music_override_comes_only_from_the_stored_marker(ienv: InterpretEnv) -> None:
    class_id, first_job = await ienv.interpreted(MUSIC_READING, spotify=False)
    stored = ienv.interpretation(class_id)["questions"]
    assert (stored[0].get("assumed_index"), stored[0]["no_music_index"]) == (None, 2)
    assert stored[1].get("no_music_index") is None

    async def reread(body: dict[str, Any]) -> UUID:
        made = await ienv.renv.browser.post(path(class_id), body)
        assert made.status_code == 201, made.text
        job = UUID(made.json()["job_id"])
        await ienv.renv.browser.post(f"/api/jobs/{job}/cancel", None)
        return job

    typed = {
        "interpret_job_id": str(first_job),
        "answers": [{"question_index": 0, "text": "no music"}],
    }
    for body in (typed, option(first_job, 0, 0), option(first_job, 1, 0)):
        # typed words (#1137), an unmarked option and the other question's "No music" option
        assert ienv.stored_input(await reread(body)).get("music_override") is None
    # the browser cannot set the override or anything else through the body
    for extra in ({"music_override": {"source": "none"}}, {"soundless": True}):
        refused = await ienv.renv.browser.post(path(class_id), option(first_job, 0, 0) | extra)
        assert refused.status_code == 422, refused.text
    marked = await reread(option(first_job, 0, 2))
    assert ienv.stored_input(marked)["music_override"] == {"source": "none"}


async def test_a_no_music_reading_is_refused_at_the_hand_off_while_soundless_is_off(
    ienv: InterpretEnv,
) -> None:
    class_id, first_job = await ienv.interpreted(MUSIC_READING, spotify=False)
    made = await ienv.renv.browser.post(path(class_id), option(first_job, 0, 2))
    assert made.status_code == 201, made.text  # the switch is not read for the re-read
    ienv.models(interpreter(SECOND))
    await ienv.run("interpret_request")
    assert ienv.interpretation(class_id)["music"] == {"source": "none"}
    before = ienv.counts()
    refused = await ienv.handoff(class_id)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "feature_off")
    assert (ienv.counts(), ienv.design_jobs(class_id)) == (before, [])  # no job, no credit
    soundless(True)  # the switch is on (#1543 retires it): the same reading designs
    designed = await ienv.handoff(class_id, retry=True)
    assert designed.status_code == 201, designed.text


@pytest.fixture
def picking(monkeypatch: pytest.MonkeyPatch) -> None:
    async def admits(*_args: Any) -> bool:
        return True

    monkeypatch.setattr(interpret_admit, "song_picking", admits)


async def pick_class(ienv: InterpretEnv, reading: dict[str, Any], **more: Any) -> UUID:
    """A class whose reading is a song pick (Spotify connected, nothing chosen in the form)."""
    spotify = str(await ienv.connect_spotify())
    class_id, _ = await ienv.interpreted(
        reading, spotify=False, spotify_connection_id=spotify, **more
    )
    assert ienv.interpretation(class_id)["music"] == {"source": "pick"}
    return class_id


@pytest.mark.usefixtures("picking")
async def test_a_pick_with_no_hint_is_refused_at_the_hand_off(ienv: InterpretEnv) -> None:
    class_id = await pick_class(ienv, {"questions": MUSIC_Q})
    before = ienv.counts()
    refused = await ienv.handoff(class_id)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "music_unanswered")
    assert (ienv.counts(), ienv.design_jobs(class_id)) == (before, [])  # no job, no credit


@pytest.mark.parametrize(
    ("reading", "more"),
    [
        ({"music_taste": {"hits": {"value": True, "origin": "inferred"}}}, {}),
        ({"pinned_songs": [{"song": "one climb", "at": "two sprints", "origin": "explicit"}]}, {}),
        ({}, {"music_sources": SOURCES}),
    ],
    ids=["taste", "pinned song", "music_sources"],
)
@pytest.mark.usefixtures("picking")
async def test_a_pick_with_a_hint_is_designed(
    ienv: InterpretEnv, reading: dict[str, Any], more: dict[str, Any]
) -> None:
    class_id = await pick_class(ienv, reading, **more)
    designed = await ienv.handoff(class_id)
    assert designed.status_code == 201, designed.text


@pytest.mark.usefixtures("picking")
async def test_a_pick_the_form_chose_is_a_hint(ienv: InterpretEnv) -> None:
    class_id, _ = await ienv.interpreted(
        {},
        music_override={"source": "pick"},
        spotify_connection_id=str(await ienv.connect_spotify()),
    )
    designed = await ienv.handoff(class_id)
    assert designed.status_code == 201, designed.text
