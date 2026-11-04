"""The running gate at creation and in the worker (ADR 0018 section 5 and its amendment of
2026-11-04, #509), the soundless gate's pattern (``test_soundless_jobs.py``), against Postgres as
``classbeat_app``, the fake MOWL and ``FakeProvider`` scripts. Never the network, never a real
account or model.

Covered: every switch path (environment off, nothing stored, the admin's DELETE, an invalid or an
``off`` value) leaves ``GET /api/connections`` off and ``admit`` refusing ``feature_off``; without
the web's ``fake``, every plan's route must cover running, read on every request (a route cleared
later, a plan added later); the order of ``admit``'s checks (switch, own plan, every plan); a
revise of a running class refused ``running_not_evaluated`` at creation; every refusal adds no
class, job or credit; the worker's backstops (``feature_off`` for a cleared switch, ``model_config``
for an uncovered model) before any call; cycling never reads any of it.
"""

import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest

from classbeat import design
from classbeat.design import sport
from designtools import BRIEF, FakeMowlMusic
from jobapi import new_key
from modeltools import add_result, clear_gate
from optools import PLAYLIST
from revisetools import ReviseEnv

pytestmark = pytest.mark.usefixtures("jenv")

RUN: dict[str, Any] = {"sport": "running", "length_min": 30, "intensity": "moderate"}


def gate(*, enabled: bool = True, fake: bool = True) -> None:
    sport.configure(sport.SportGate(running_enabled=enabled, fake_covers=fake))


def admin_flag(renv: ReviseEnv, value: Any, *, raw: str | None = None) -> None:
    """The stored ``running_enabled``: ``value`` as JSON, ``raw`` a JSON text (``null``, as the
    admin's DELETE leaves it), or no row for ``None``."""
    renv.sql("DELETE FROM app_settings WHERE key = 'running_enabled'")
    if value is not None or raw is not None:
        renv.sql(
            "INSERT INTO app_settings (key, value) VALUES ('running_enabled', CAST(:v AS jsonb))",
            v=raw if raw is not None else json.dumps(value),
        )


@pytest.fixture(autouse=True)
def running_on(renv: ReviseEnv) -> Iterator[None]:
    """The environment's switch on, the web's ``fake`` covering (as the e2e stack) and the admin
    flag stored ``on``; after ``renv``, whose app configured the gate from its settings (off)."""
    gate()
    admin_flag(renv, "on")
    try:
        yield
    finally:
        sport.configure(None)
        admin_flag(renv, None)
        renv.sql("DELETE FROM plans WHERE id = 'later'")
        clear_gate(renv.denv.ops.admin)


def design_body(renv: ReviseEnv, brief: dict[str, Any] = RUN) -> dict[str, Any]:
    values = {
        "title": "Tuesday run",
        "brief": brief,
        "music": {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST},
        "mowl_connection_id": str(renv.browser.connection_id),
    }
    return {"kind": design.KIND, "input": values}


async def create(renv: ReviseEnv, body: dict[str, Any]) -> Any:
    return await renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


def counts(renv: ReviseEnv) -> tuple[int, int, int]:
    """(classes, jobs, credit ledger rows) of the user: what a refused create must not add to."""
    o = renv.owner
    return (
        renv.sql("SELECT count(*) FROM classes WHERE owner_id = :o", o=o).scalar_one(),
        renv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=o).scalar_one(),
        renv.sql("SELECT count(*) FROM credit_ledger WHERE owner_id = :o", o=o).scalar_one(),
    )


async def refused_with(renv: ReviseEnv, reason: str) -> None:
    before = counts(renv)
    refused = await create(renv, design_body(renv))
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == reason
    assert counts(renv) == before  # no class, no job, no credit


async def connections_flag(renv: ReviseEnv) -> bool:
    got = await renv.browser.get("/api/connections")
    assert got.status_code == 200, got.text
    return got.json()["running_enabled"]


def route(renv: ReviseEnv, *keys: str) -> None:
    """A passing design-group result on the running suite and a ``design`` route at it for each
    of ``keys`` (setup, as the superuser)."""
    result = add_result(renv.denv.ops.admin)
    for key in keys:
        renv.sql(
            "INSERT INTO model_routes (route_key, stage_group, provider, model, effort, "
            "eval_result_id, set_at, set_by, gate_passed) SELECT :k, 'design', provider, model, "
            "effort, id, now(), 'setup', true FROM model_eval_results WHERE id = :r",
            k=key,
            r=result,
        )


def plan_keys(renv: ReviseEnv) -> list[str]:
    return list(renv.sql("SELECT DISTINCT route_key FROM plans ORDER BY 1").scalars().all())


async def own_key(renv: ReviseEnv) -> str:
    """The ``route_key`` of the user's effective plan (what ``with_plan`` gives the job)."""
    got = await renv.browser.get("/api/account/plan")
    assert got.status_code == 200, got.text
    plan = got.json()["plan"]
    return renv.sql("SELECT route_key FROM plans WHERE id = :p", p=plan).scalar_one()


def forget_jobs(renv: ReviseEnv) -> None:
    renv.sql("DELETE FROM jobs WHERE owner_id = :o", o=renv.owner)
    renv.sql("DELETE FROM credit_ledger WHERE owner_id = :o", o=renv.owner)


# --- the gate at creation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", ["environment_off", "nothing_stored", "deleted", "invalid", "stored_off"]
)
async def test_every_switch_path_leaves_the_flag_off_and_admit_refusing_feature_off(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic, case: str
) -> None:
    """On only with the environment on **and** a stored ``on``: an environment-only on, nothing
    stored, the admin's DELETE (JSON ``null``), an invalid or an ``off`` value are all off."""
    if case == "environment_off":
        gate(enabled=False)  # the database can never switch it on past the environment
    elif case == "nothing_stored":
        admin_flag(renv, None)
    elif case == "deleted":
        admin_flag(renv, None, raw="null")
    elif case == "invalid":
        admin_flag(renv, "yes")
    else:
        admin_flag(renv, "off")
    assert await connections_flag(renv) is False
    await refused_with(renv, "feature_off")
    assert (fake_mowl.reads, fake_mowl.attaches) == ([], [])
    cycling = await create(renv, design_body(renv, BRIEF))  # cycling never reads the flag
    assert cycling.status_code == 201, cycling.text


async def test_the_flag_needs_every_plans_route_to_cover_running_read_on_every_request(
    renv: ReviseEnv,
) -> None:
    """Without the web's own ``fake``, the effective flag and ``admit`` need a covering
    ``design`` route for every plan's ``route_key``, checked on each read: a route cleared later
    or a plan added later turns it off with nothing stored."""
    gate(fake=False)
    assert await connections_flag(renv) is False  # stored ``on``, but no route: the bootstrap
    await refused_with(renv, "running_not_evaluated")
    keys = plan_keys(renv)
    route(renv, *keys)
    assert await connections_flag(renv) is True
    made = await create(renv, design_body(renv))
    assert made.status_code == 201, made.text
    forget_jobs(renv)
    # Another plan's route cleared later: the user's own plan is covered, the flag is not.
    mine = await own_key(renv)
    other = next(k for k in keys if k != mine)
    renv.sql("DELETE FROM model_routes WHERE route_key = :k", k=other)
    assert await connections_flag(renv) is False
    await refused_with(renv, "feature_off")
    route(renv, other)
    assert await connections_flag(renv) is True
    # A plan added later, with a route key of its own and no route.
    renv.sql(
        "INSERT INTO plans (id, name, rank, classes_per_month, revisions_per_class, "
        "concurrent_jobs, monthly_cost_micros, route_key) "
        "VALUES ('later', 'Later', 99, 3, 3, 1, 10000000, 'later')"
    )
    assert await connections_flag(renv) is False
    await refused_with(renv, "feature_off")
    renv.sql("DELETE FROM plans WHERE id = 'later'")
    # The user's own plan's route cleared later.
    renv.sql("DELETE FROM model_routes WHERE route_key = :k", k=mine)
    assert await connections_flag(renv) is False
    await refused_with(renv, "running_not_evaluated")
    # A newer failed result for the same model: the route is stale and covers nothing.
    route(renv, mine)
    assert await connections_flag(renv) is True
    add_result(renv.denv.ops.admin, passed=False, age_minutes=-1)
    assert await connections_flag(renv) is False
    await refused_with(renv, "running_not_evaluated")


@pytest.mark.parametrize(
    ("switch", "own", "others", "reason"),
    [
        (False, False, False, "feature_off"),  # 1. the switch first, whatever is covered
        (False, True, True, "feature_off"),
        (True, False, True, "running_not_evaluated"),  # 2. the job's own plan next
        (True, False, False, "running_not_evaluated"),  # (before every plan, or 2 is dead)
        (True, True, False, "feature_off"),  # 3. then every plan
        (True, True, True, None),
    ],
)
async def test_admit_checks_the_switch_then_its_own_plan_then_every_plan(
    renv: ReviseEnv, switch: bool, own: bool, others: bool, reason: str | None
) -> None:
    gate(fake=False)
    admin_flag(renv, "on" if switch else "off")
    keys, mine = plan_keys(renv), await own_key(renv)
    route(
        renv,
        *([mine] if own else []),
        *([k for k in keys if k != mine] if others else []),
    )
    if reason is None:
        made = await create(renv, design_body(renv))
        assert made.status_code == 201, made.text
        assert counts(renv)[2] == 1  # the credit is reserved only once admitted
    else:
        await refused_with(renv, reason)


async def test_a_revise_of_a_running_class_is_refused_at_creation_without_coverage(
    renv: ReviseEnv,
) -> None:
    """A running class designed while covered; its plan's route cleared later: the chat request
    is refused 409 ``running_not_evaluated`` before the job row and the credit."""
    renv.use("running_first_draft")
    made = await create(renv, design_body(renv))
    assert made.status_code == 201, made.text
    await renv.run(design.KIND)
    job_id = UUID(made.json()["id"])
    assert renv.denv.ops.row(job_id).state == "completed"
    class_id, base = UUID(made.json()["class_id"]), renv.denv.revisions(job_id)[0].id
    gate(fake=False)  # no route at all now
    renv.use("running_revise")
    before = counts(renv)
    refused = await renv.ask(class_id, base)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "running_not_evaluated"
    assert counts(renv) == before
    admin_flag(renv, None)  # and the switch cleared: feature_off first
    refused = await renv.ask(class_id, base)
    assert refused.json()["detail"]["reason"] == "feature_off"
    assert counts(renv) == before


# --- the worker's backstops -------------------------------------------------------------------


async def test_a_queued_running_design_whose_switch_was_cleared_ends_feature_off_calling_nothing(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    renv.use("running_first_draft")
    made = await create(renv, design_body(renv))
    assert made.status_code == 201, made.text
    admin_flag(renv, None)  # the admin's DELETE while the job waited
    await renv.run(design.KIND)
    row = renv.denv.ops.row(UUID(made.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "feature_off")
    assert renv.providers == []  # no model was even built
    assert (fake_mowl.reads, fake_mowl.attaches) == ([], [])


async def test_a_running_design_admitted_before_its_route_was_cleared_ends_model_config(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    """The race with the admin (no lock shared with the route writes): admitted while covered,
    the route cleared before the run; the worker's per-attempt check ends it ``model_config``
    before any MOWL or model call."""
    gate(fake=False)
    route(renv, *plan_keys(renv))
    renv.use("running_first_draft")
    made = await create(renv, design_body(renv))
    assert made.status_code == 201, made.text
    clear_gate(renv.denv.ops.admin)
    renv.running_covered = False  # what the worker resolves now: the bootstrap choice
    renv.use("running_first_draft")  # the runtime picks it up
    await renv.run(design.KIND)
    row = renv.denv.ops.row(UUID(made.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "model_config")
    assert all(p.sent == [] for p in renv.providers)
    assert (fake_mowl.reads, fake_mowl.attaches) == ([], [])
