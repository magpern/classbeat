"""Durable usage (ADR 0007 section 10; US1, #145): ``agent_runs`` written with ``job_kind`` and
``price_version``, ``GET /api/usage`` and ``usage`` in ``GET /api/jobs/{id}``. Fakes only."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from agentfake import CONFIG, analysed, brief, provider
from classbeat.agent import DesignContext, JobStageHost, design_stage, run_stage
from classbeat.agent import prices as price_module
from classbeat.agent.prices import PRICES_VERSION
from classbeat.jobs import Complete, JobRun
from classbeat.usage import job_usage, month_usage
from ctx import make_context
from jobapi import Browser
from jobcore import KIND, JobsEnv, registry_for, run_next, store_draft

pytestmark = pytest.mark.usefixtures("jenv")

NOTES = ["estimate_at_list_prices", "sdk_retries", "lost_lease_turn", "failed_stream_turn"]
THIS_MONTH = datetime.now(UTC).strftime("%Y-%m")


def design_kind(ctx: DesignContext) -> Any:
    async def run(job_run: JobRun) -> Complete:
        await run_stage(
            JobStageHost(job_run),
            provider("good_first_draft"),
            design_stage(ctx),
            ctx.first_message(),
            CONFIG,
        )
        return Complete(store_draft)

    return registry_for(run)


def plant(
    jenv: JobsEnv,
    owner: UUID,
    job_id: UUID | None,
    *,
    kind: str = "design_draft",
    model: str = "fake-model",
    tokens: tuple[int, int, int, int] = (100, 20, 30, 40),
    cost: int = 1000,
    outcome: str | None = "submitted",
    started: datetime | None = None,
    attempt: int = 1,
) -> None:
    jenv.sql(
        "INSERT INTO agent_runs (owner_id, job_id, job_kind, price_version, stage, attempt, "
        "provider, model, effort, prompt_version, input_tokens, output_tokens, "
        "cache_read_tokens, cache_write_tokens, cost_micros, outcome, started_at, finished_at) "
        "VALUES (:o, :j, :k, 'v', 'design', :a, 'fake', :m, 'medium', 'x', :i, :out, :cr, :cw, "
        ":c, :oc, :s, CASE WHEN CAST(:oc AS text) IS NULL THEN NULL ELSE now() END)",
        o=owner, j=job_id, k=kind, a=attempt, m=model, i=tokens[0], out=tokens[1], cr=tokens[2],
        cw=tokens[3], c=cost, oc=outcome, s=started or datetime.now(UTC),
    )  # fmt: skip


async def test_a_fake_design_is_recorded_and_the_monthly_view_is_the_sum_of_its_rows(
    jenv: JobsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = DesignContext.build(brief(), analysed(monkeypatch))
    owner, job_id = jenv.job()
    await run_next(jenv, design_kind(ctx))
    [row] = jenv.sql("SELECT * FROM agent_runs WHERE job_id = :j", j=job_id).all()
    assert (row.job_kind, row.price_version) == (KIND, PRICES_VERSION)
    assert row.cost_micros > 0
    user = make_context(owner)
    month = await month_usage(jenv.factory, user, THIS_MONTH)
    assert month is not None
    assert month.total.runs == 1
    assert month.total.cost_micros == row.cost_micros
    assert month.total.input_tokens == row.input_tokens
    assert month.total.output_tokens == row.output_tokens
    assert [(r.job_kind, r.model) for r in month.by_kind_model] == [(KIND, row.model)]
    used = await job_usage(jenv.factory, user, job_id)
    assert used is not None
    assert (used.cost_micros, used.input_tokens) == (row.cost_micros, row.input_tokens)


async def test_the_price_version_stored_is_not_changed_by_editing_the_table(
    jenv: JobsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = DesignContext.build(brief(), analysed(monkeypatch))
    _, job_id = jenv.job()
    await run_next(jenv, design_kind(ctx))
    monkeypatch.setattr(price_module, "PRICES_VERSION", "2099-01-01")
    monkeypatch.setitem(
        price_module.ANTHROPIC_PRICES,
        ("anthropic", "claude-opus-5-5"),
        price_module.Price(Decimal(1), Decimal(1), Decimal(1), Decimal(1)),
    )
    stored = jenv.sql("SELECT price_version FROM agent_runs WHERE job_id = :j", j=job_id)
    assert stored.scalar_one() == PRICES_VERSION != "2099-01-01"


async def test_two_attempts_and_an_open_crashed_run_all_count(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    job_id = UUID((await alice.create()).json()["id"])
    plant(jenv, alice.user_id, job_id, outcome="model_unavailable", cost=500, attempt=1)
    plant(jenv, alice.user_id, job_id, outcome=None, tokens=(7, 0, 0, 0), cost=300, attempt=2)
    plant(jenv, alice.user_id, job_id, tokens=(1, 2, 3, 4), cost=200, attempt=3)
    shown = (await alice.get(f"/api/jobs/{job_id}")).json()
    assert shown["usage"] == {
        "runs": 3, "input_tokens": 108, "output_tokens": 22, "cache_read_tokens": 33,
        "cache_write_tokens": 44, "cost_micros": 1000,
    }  # fmt: skip
    month = (await alice.get("/api/usage")).json()
    assert month["total"]["cost_micros"] == 1000
    assert (month["total"]["runs"], month["total"]["jobs"]) == (3, 1)
    assert month["classes_designed"] == 1


async def test_a_job_without_runs_has_no_usage(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    job_id = (await alice.create()).json()["id"]
    assert (await alice.get(f"/api/jobs/{job_id}")).json()["usage"] is None


async def test_usage_survives_deleting_the_class_and_goes_with_the_account(
    jenv: JobsEnv, browsers: Any
) -> None:
    alice: Browser = await browsers()
    created = (await alice.create()).json()
    job_id = UUID(created["id"])
    plant(jenv, alice.user_id, job_id, cost=900)
    jenv.sql("DELETE FROM classes WHERE id = :c", c=UUID(created["class_id"]))
    assert jenv.sql("SELECT count(*) FROM jobs WHERE id = :j", j=job_id).scalar_one() == 0
    month = (await alice.get("/api/usage")).json()
    assert month["total"]["cost_micros"] == 900
    assert month["classes_designed"] == 1
    [row] = jenv.sql("SELECT job_id FROM agent_runs WHERE owner_id = :o", o=alice.user_id).all()
    assert row.job_id is None
    jenv.sql("DELETE FROM users WHERE id = :u", u=alice.user_id)
    left = jenv.sql("SELECT count(*) FROM agent_runs WHERE owner_id = :o", o=alice.user_id)
    assert left.scalar_one() == 0


async def test_another_users_usage_and_other_months_are_not_included(
    jenv: JobsEnv, browsers: Any
) -> None:
    alice: Browser = await browsers()
    bob: Browser = await browsers()
    a_job = UUID((await alice.create()).json()["id"])
    b_job = UUID((await bob.create()).json()["id"])
    plant(jenv, alice.user_id, a_job, cost=100)
    plant(jenv, bob.user_id, b_job, cost=7000)
    last_month = datetime.now(UTC).replace(day=1) - timedelta(days=1)
    plant(jenv, alice.user_id, a_job, cost=50000, started=last_month, attempt=2)
    mine = (await alice.get("/api/usage")).json()
    assert mine["total"]["cost_micros"] == 100
    assert mine["notes"] == NOTES
    assert (await alice.get(f"/api/jobs/{b_job}")).status_code == 404  # no usage via a job id
    theirs = (await bob.get("/api/usage")).json()
    assert theirs["total"]["cost_micros"] == 7000


async def test_the_month_parameter(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    job_id = UUID((await alice.create()).json()["id"])
    plant(jenv, alice.user_id, job_id, cost=11)
    explicit = await alice.get(f"/api/usage?month={THIS_MONTH}")
    assert explicit.status_code == 200
    assert explicit.json()["month"] == THIS_MONTH
    for bad in ("2026-13", "2026-1", "abc", "2026-10-01", chr(0xFF12) + "026-10"):
        response = await alice.get(f"/api/usage?month={bad}")
        assert (response.status_code, response.json()["detail"]["reason"]) == (
            422,
            "invalid_month",
        ), bad
    old = await alice.get("/api/usage?month=2001-01")
    assert (old.status_code, old.json()["total"]["runs"], old.json()["notes"]) == (200, 0, NOTES)
    empty = (await alice.get("/api/usage?month=" + THIS_MONTH)).json()
    assert empty["notes"] == NOTES


async def test_the_usage_needs_a_session(browsers: Any) -> None:
    alice: Browser = await browsers()
    assert (await alice.client.get("/api/usage")).status_code == 401


async def test_per_kind_and_model_breakdown(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    first = UUID((await alice.create()).json()["id"])
    second = UUID((await alice.create()).json()["id"])
    plant(jenv, alice.user_id, first, model="m1", cost=10)
    plant(jenv, alice.user_id, second, model="m2", cost=20)
    plant(jenv, alice.user_id, second, kind="revise_draft", model="m2", cost=30, attempt=2)
    month = (await alice.get("/api/usage")).json()
    got = {(r["job_kind"], r["model"]): r["cost_micros"] for r in month["by_kind_model"]}
    assert got == {
        ("design_draft", "m1"): 10,
        ("design_draft", "m2"): 20,
        ("revise_draft", "m2"): 30,
    }
    assert (month["classes_designed"], month["revisions"]) == (2, 1)
    assert month["total"]["cost_micros"] == 60
