"""The ``interpret_request`` kind (ADR 0029 section 5, MA1b-3) through the jobs API and the worker
side, against Postgres as ``classbeat_app``, the fake MOWL and ``FakeProvider``: the admit (a new
class, the request as message seq 1, no credit, the server fields, the cost cap and the per-user
limit), the worker (the interpretation stored under the fence, nothing enqueued) and a failed
interpret (nothing stored, no credit)."""

from typing import Any
from uuid import UUID

import pytest

from classbeat.design import sport as sport_module
from classbeat.design.interpret_kind import InterpretRequestInput
from classbeat.design.interpretation_row import StoredInterpretation
from classbeat.jobs.credits import QuotaRefused
from designtools import PLAYLIST
from interprettools import REQUEST, InterpretEnv, interpreter, refusing

pytestmark = pytest.mark.usefixtures("jenv")


async def test_admit_creates_the_class_and_the_first_message_and_takes_no_credit(
    ienv: InterpretEnv,
) -> None:
    before = ienv.counts()
    made = await ienv.start()
    assert made.status_code == 201, made.text
    job_id, class_id = UUID(made.json()["id"]), UUID(made.json()["class_id"])
    assert ienv.counts() == (before[0] + 1, before[1] + 1, before[2])  # a class, a job, no credit
    row = ienv.sql("SELECT credit_state, kind FROM jobs WHERE id = :j", j=job_id).one()
    assert (row.credit_state, row.kind) == ("none", "interpret_request")
    title = ienv.sql("SELECT title FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert title == "A 30 minute class with one climb and two sprints, please"
    messages = ienv.renv.messages(class_id)
    assert [(m.seq, m.role, m.body, m.job_id) for m in messages] == [(1, "user", REQUEST, job_id)]
    stored = ienv.stored_input(job_id)
    assert stored["sports"] == ["cycling"]
    assert (stored["pick_ready"], stored["swedebeat_mixes"]) == (False, False)
    assert stored["route_key"]
    assert stored["plan_gates"]["max_class_minutes"] >= 10


@pytest.mark.parametrize(
    "more",
    [
        {"class_id": "00000000-0000-4000-8000-000000000000"},
        {"route_key": "default"},
        {"sports": ["cycling", "running"]},
        {"pick_ready": True},
        {"music_override": {"source": "pick"}},  # a pick needs the Spotify connection
        {"request": " "},
        {"request": "x" * 2001},
    ],
)
async def test_invalid_input_and_server_fields_are_refused(
    ienv: InterpretEnv, more: dict[str, Any]
) -> None:
    before = ienv.counts()
    refused = await ienv.start(**more)
    assert refused.status_code == 422, refused.text
    assert ienv.counts() == before


async def test_a_request_whose_brief_could_not_fit_the_class_is_refused(ienv: InterpretEnv) -> None:
    with pytest.raises(ValueError, match="too long"):
        InterpretRequestInput.model_validate(
            {"request": "å" * 2000, "mowl_connection_id": str(ienv.renv.browser.connection_id)}
        )
    assert (await ienv.start(request="å" * 2000)).status_code == 422


async def test_a_spotify_connection_that_is_not_the_users_is_refused(ienv: InterpretEnv) -> None:
    before = ienv.counts()
    refused = await ienv.start(spotify_connection_id="00000000-0000-4000-8000-000000000000")
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "not_connected"
    assert ienv.counts() == before


async def test_the_cost_cap_refuses_it(ienv: InterpretEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    async def capped(db: Any, ctx: Any, limits: Any = None) -> None:
        raise QuotaRefused("cost", limit=1, used=1, plan="pro")

    monkeypatch.setattr("classbeat.quota.check_cost", capped)
    before = ienv.counts()
    refused = await ienv.start()
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "quota"
    assert ienv.counts() == before  # no class, no job, no message


async def test_it_counts_toward_the_per_user_limit(ienv: InterpretEnv) -> None:
    answers = [(await ienv.start()).status_code for _ in range(4)]
    assert 429 in answers  # ``pro``'s jobs at once (``plantools``): queued interprets count
    refused = await ienv.start()
    assert refused.status_code == 429
    assert refused.json()["detail"]["reason"] == "busy"


async def test_the_worker_stores_the_interpretation_and_enqueues_nothing(
    ienv: InterpretEnv,
) -> None:
    class_id, job_id = await ienv.interpreted()
    stored = StoredInterpretation.model_validate(ienv.interpretation(class_id))
    assert stored.job_id == job_id
    assert stored.brief["length_min"] == 30
    assert stored.brief["climbs"] == 1
    assert stored.brief["text"] == REQUEST
    assert stored.music == {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST}
    assert stored.refusal is None
    paths = {i.path: i for i in stored.items}
    assert paths["length_min"].quote == "30 minute"
    assert paths["music"].value == "first_playlist"
    assert ienv.design_jobs(class_id) == []  # it never enqueues
    assert ienv.credits() == 0
    sent = ienv.renv.denv.ops.sql(
        "SELECT count(*) FROM agent_runs WHERE job_id = :j", j=job_id
    ).scalar_one()
    assert sent == 1  # the model run is recorded: its cost counts toward the cap
    current = ienv.sql("SELECT current_revision_id, brief FROM classes WHERE id = :c", c=class_id)
    assert tuple(current.one()) == (None, None)
    assert ienv.renv.revisions(class_id) == []  # no revision: no origin list needs the kind


async def test_the_interpreter_sees_names_and_never_ids(ienv: InterpretEnv) -> None:
    fake = interpreter()
    ienv.models(fake)
    made = await ienv.start()
    assert made.status_code == 201
    await ienv.run("interpret_request")
    sent = "".join(str(s) for s in fake.sent)
    assert "Invented Tuesday Mix" in sent  # the playlist's name, as MOWL lists it
    assert PLAYLIST not in sent
    assert str(ienv.renv.browser.connection_id) not in sent


async def test_a_failed_interpret_stores_nothing_and_uses_no_credit(ienv: InterpretEnv) -> None:
    ienv.models(refusing())
    made = await ienv.start()
    job_id, class_id = UUID(made.json()["id"]), UUID(made.json()["class_id"])
    await ienv.run("interpret_request")
    row = ienv.renv.denv.ops.row(job_id)
    assert row.state == "failed"
    assert ienv.interpretation(class_id) is None
    assert ienv.credits() == 0
    answer = await ienv.handoff(class_id)
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "not_ready")
    view = await ienv.renv.browser.get(f"/api/classes/{class_id}/interpretation")
    assert (view.status_code, view.json()["detail"]["reason"]) == (409, "not_ready")
    assert ienv.design_jobs(class_id) == []


async def test_the_class_list_shows_the_interpretations_sport(ienv: InterpretEnv) -> None:
    class_id, _ = await ienv.interpreted()
    ienv.sql(
        "UPDATE classes SET interpretation = jsonb_set(interpretation, '{brief,sport}', "
        "'\"running\"') WHERE id = :c",
        c=class_id,
    )
    listed = await ienv.renv.browser.get("/api/classes")
    assert listed.status_code == 200, listed.text
    sport = {c["id"]: c["sport"] for c in listed.json()["classes"]}
    assert sport[str(class_id)] == "running"


async def test_an_interpretation_does_not_fix_the_classs_sport(ienv: InterpretEnv) -> None:
    """``design.sport``'s class sport reads the stored brief and the design kinds' input only: a
    class with just its interpretation is not fixed, so the design job's own brief decides."""
    class_id, _ = await ienv.interpreted()
    found = ienv.sql(
        str(sport_module._CLASS_SPORT),  # pyright: ignore[reportPrivateUsage]
        owner_id=ienv.owner,
        class_id=class_id,
    ).one()
    assert (found.fixed, found.brief) == (False, None)
