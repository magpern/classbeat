"""The route key at job creation (ADR 0011 section 6, ADR 0012 section 5; AD5 #223): the job's
``route_key`` is the effective plan's, stored with the input by the server alone; a plan change
applies to the next job only; a browser can never send it; every model kind sets it."""

from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel

from classbeat import design, songs
from classbeat.db import system_session
from classbeat.jobs import KINDS
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.models.routes import RouteUnusable, bootstrap_choice, resolve_route, set_route
from designtools import DesignEnv, FakeMowlMusic
from jobapi import Browser
from modeltools import OPUS, add_result
from optools import TOKEN_A
from revisetools import ReviseEnv
from test_design_api import body

pytestmark = pytest.mark.usefixtures("jenv")


class Nothing(BaseModel):
    pass


EMPTY = Nothing()


def admitted(outcome: Any) -> Any:
    assert isinstance(outcome, Admitted)
    return dict(outcome.fields)


def set_plan_keys(denv: DesignEnv, **keys: str) -> None:
    for plan, key in keys.items():
        denv.ops.sql("UPDATE plans SET route_key = :k WHERE id = :p", k=key, p=plan)


@pytest.fixture(autouse=True)
def restore_plan_keys(denv: DesignEnv) -> Any:
    yield
    denv.ops.sql("UPDATE plans SET route_key = id")  # migration 0014: a key of its own


async def create(browser: Browser, name: str, sent: dict[str, Any] | None = None) -> Any:
    return await browser.post("/api/jobs", sent or body(browser), {"Idempotency-Key": name * 2})


def stored_key(denv: DesignEnv, response: Any) -> str:
    assert response.status_code == 201, response.text
    return denv.ops.row(UUID(response.json()["id"])).input["route_key"]


async def test_the_job_carries_the_effective_plans_route_key_and_a_change_applies_next(
    browsers: Any, denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    browser: Browser = await browsers()
    denv.app.app.dependency_overrides.clear()
    await denv.ops.connect(browser.user_id, TOKEN_A)
    set_plan_keys(denv, pro="pro_route", free="free_route")
    first = await create(browser, "route-key-first-0001")
    assert stored_key(denv, first) == "pro_route"
    set_plan_keys(denv, pro="pro_route_two")
    still = denv.ops.row(UUID(first.json()["id"])).input["route_key"]
    assert still == "pro_route"  # the running job keeps the key it was created with
    await denv.run_next()  # the first job ends, so the next one fits the plan's concurrency
    second = await create(browser, "route-key-second-001")
    assert stored_key(denv, second) == "pro_route_two"
    # The grant ends: the user is on free, whose key is another one.
    await denv.run_next()
    denv.ops.sql(
        "UPDATE entitlements SET revoked_at = now() WHERE owner_id = :o", o=browser.user_id
    )
    third = await create(browser, "route-key-third-0001")
    assert stored_key(denv, third) == "free_route"


async def test_a_browser_can_never_send_a_route_key(browsers: Any, denv: DesignEnv) -> None:
    browser: Browser = await browsers()
    denv.app.app.dependency_overrides.clear()
    set_plan_keys(denv, pro="pro_route")
    for value in ("pro_route", "default", "other"):
        sent = body(browser)
        sent["input"]["route_key"] = value
        refused = await create(browser, f"route-key-sent-{value}", sent)
        assert refused.status_code == 422, refused.text
    assert denv.ops.sql("SELECT count(*) FROM jobs").scalar_one() == 0


async def test_a_revise_job_carries_the_plans_route_key_too(renv: ReviseEnv) -> None:
    set_plan_keys(renv.denv, pro="pro_route")
    class_id, base = await renv.design()
    job_id, _ = await renv.revise(class_id, base, "revise_good")
    assert renv.denv.ops.row(job_id).input["route_key"] == "pro_route"


def test_every_model_kind_sets_the_key_in_its_create_path() -> None:
    kinds = [
        design.DESIGN_DRAFT,
        design.REVISE_DRAFT,
        songs.DESIGN_WITH_SONGS,
        songs.REVISE_SONGS,
    ]
    for kind in kinds:
        assert KINDS.get(kind.name) is kind
        assert "route_key" in kind.server_fields, kind.name
        assert kind.admit is not None
        assert getattr(kind.admit, "__qualname__", "") == "with_route_key.<locals>.admit", kind.name


async def test_the_wrapper_keeps_a_refusal_and_the_kinds_own_fields(
    browsers: Any, denv: DesignEnv
) -> None:
    from classbeat.context import UserContext
    from classbeat.db import user_session
    from classbeat.quota.route import with_route_key
    from ctx import make_context

    browser: Browser = await browsers()
    set_plan_keys(denv, pro="pro_route")
    ctx: UserContext = make_context(browser.user_id)

    async def refuses(db: Any, ctx: Any, parsed: Any, /) -> Refused:
        return Refused("nope")

    async def adds(db: Any, ctx: Any, parsed: Any, /) -> Admitted:
        return Admitted({"request_message_id": "kept", "route_key": "from_the_kind"})

    async with user_session(denv.ops.factory, ctx) as db:
        assert await with_route_key(refuses)(db, ctx, EMPTY) == Refused("nope")
        fields = admitted(await with_route_key(adds)(db, ctx, EMPTY))
        assert fields == {"request_message_id": "kept", "route_key": "pro_route"}
        assert admitted(await with_route_key()(db, ctx, EMPTY)) == {"route_key": "pro_route"}


async def test_a_route_set_for_pro_applies_to_a_pro_users_job_and_not_to_a_free_users(
    browsers: Any, denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    """Migration 0014: each plan's key is its id. The route is on ``pro`` only, so the pro user's
    job resolves it (here: found, and unusable without the worker's key file) and the free user's
    job gets the bootstrap choice."""
    browser: Browser = await browsers()
    denv.app.app.dependency_overrides.clear()
    await denv.ops.connect(browser.user_id, TOKEN_A)
    add_result(denv.admin)
    web = denv.app.settings
    async with system_session(denv.ops.factory) as db:
        await set_route(db, "pro", "design", "anthropic", OPUS, "medium", "admin", settings=web)
    pro_job = await create(browser, "route-key-pro-job-0001")
    assert stored_key(denv, pro_job) == "pro"
    await denv.run_next()
    revoke = "UPDATE entitlements SET revoked_at = now() WHERE owner_id = :o"
    denv.ops.sql(revoke, o=browser.user_id)
    free_job = await create(browser, "route-key-free-job-001")
    assert stored_key(denv, free_job) == "free"
    async with system_session(denv.ops.factory) as db:
        with pytest.raises(RouteUnusable) as found:
            await resolve_route(db, "pro", "design", settings=web)
        assert found.value.reason == "no_key"  # the route exists; its key file is the worker's
        assert await resolve_route(db, "free", "design", settings=web) == bootstrap_choice(web)
        assert await resolve_route(db, "instructor", "design", settings=web) == bootstrap_choice(
            web
        )
