"""The admin Jobs view (ADR 0011 section 6, AD3 #221): the gate, one audit row per call, counts
per state from ``jobs.state``, another user's waiting approval and uncertain external operation
(owner email, kind, since, job state), recent failures by category, and no planted secret, brief,
input or external value in the response."""

import json
import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from adminviews import audit, new_class, new_job, reason, stepped_admin
from authtools import Env, running_env
from classbeat.admin.routes import admin_area_routes, gate_problems

JOBS = "/api/admin/jobs"


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as env:
        yield env


def new_operation(
    conn: Connection,
    owner: uuid.UUID,
    job: uuid.UUID | None,
    class_id: uuid.UUID,
    state: str,
    *,
    external_id: str | None = None,
    result: str = "",
    key: str | None = None,
) -> None:
    conn.execute(
        text(
            "INSERT INTO external_operations (owner_id, job_id, class_id, kind, idempotency_key, "
            "request_hash, state, external_id, result, lease_version) VALUES (:o, :j, :c, "
            "'spotify_create_playlist', :k, :h, :s, :x, CAST(:r AS jsonb), 1)"
        ),
        {
            "o": owner,
            "j": job,
            "c": class_id,
            "k": key or secrets.token_urlsafe(18),
            "h": secrets.token_bytes(32),
            "s": state,
            "x": external_id,
            "r": json.dumps({"url": result} if result else {}),
        },
    )


async def test_the_router_guard_covers_the_jobs_route(env: Env) -> None:
    assert gate_problems(env.app.routes) == []
    assert JOBS in {path for path, _ in admin_area_routes(env.app.routes)}


async def test_non_admins_get_403_and_an_admin_without_mfa_is_audited(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    plain = await make_actor(await env.user("plain@example.com"), mfa=True)
    response = await plain.get(JOBS)
    assert (response.status_code, reason(response)) == (403, "forbidden")
    assert superuser.execute(text("SELECT count(*) FROM admin_audit")).scalar_one() == 0
    admin = await env.user("a@example.com", role="admin")
    no_mfa = await (await make_actor(admin)).get(JOBS)
    assert (no_mfa.status_code, reason(no_mfa)) == (403, "mfa_required")
    assert audit(superuser, admin) == [("admin.request", "refused", "mfa_required")]


async def test_the_view_shows_another_users_approval_and_uncertain_operation(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    rider = await env.user("rider@example.com")
    other = await env.user("other@example.com")
    plant = {
        "brief": "PLANTED brief " + secrets.token_hex(6),
        "input": "PLANTED input " + secrets.token_hex(6),
        "external": "7Planted" + secrets.token_hex(7),
        "url": "https://open.spotify.com/playlist/7Planted" + secrets.token_hex(5),
        "key": "spotify:planted-" + secrets.token_hex(6),
    }
    waiting_class = new_class(superuser, rider, brief=plant["brief"])
    new_job(superuser, rider, "design_with_songs", "waiting_for_approval",
            credit_state="reserved", job_input=plant["input"], class_id=waiting_class)  # fmt: skip
    op_class = new_class(superuser, rider, brief=plant["brief"])
    running = new_job(superuser, rider, "design_with_songs", "running", class_id=op_class,
                      job_input=plant["input"])  # fmt: skip
    new_operation(superuser, rider, running, op_class, "uncertain", key=plant["key"])
    done_class = new_class(superuser, other)
    new_operation(superuser, other, None, done_class, "succeeded",
                  external_id=plant["external"], result=plant["url"])  # fmt: skip
    new_operation(superuser, other, None, done_class, "uncertain")  # its job went with the class
    new_job(superuser, other, "design_draft", "failed", category="model_unavailable")
    new_job(superuser, other, "design_draft", "failed", category="model_unavailable")
    new_job(superuser, rider, "save_workout", "failed", category="uncertain_write")
    new_job(superuser, other, "design_draft", "failed", category="internal", finished_days_ago=10)
    new_job(superuser, other, "design_draft", "completed")

    response = await actor.get(JOBS)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    states = {s["state"]: s["count"] for s in body["states"]}
    assert states == {"waiting_for_approval": 1, "running": 1, "failed": 4, "completed": 1}
    (waiting,) = body["waiting_approvals"]
    assert (waiting["user_id"], waiting["email"], waiting["kind"]) == (
        str(rider), "rider@example.com", "design_with_songs"
    )  # fmt: skip
    assert waiting["waiting_since"]
    assert waiting["expires_at"]
    uncertain = sorted(
        (o["email"], o["kind"], o["job_state"]) for o in body["uncertain_operations"]
    )
    assert uncertain == [
        ("other@example.com", "spotify_create_playlist", None),
        ("rider@example.com", "spotify_create_playlist", "running"),
    ]
    assert all(o["since"] for o in body["uncertain_operations"])
    failures = [(f["kind"], f["category"], f["count"]) for f in body["failures"]]
    assert failures == [
        ("design_draft", "model_unavailable", 2),
        ("save_workout", "uncertain_write", 1),
    ]
    # Only what the issue allows: no job or operation ids, inputs, keys, external ids or URLs.
    assert set(waiting) == {"user_id", "email", "kind", "waiting_since", "expires_at"}
    assert set(body["uncertain_operations"][0]) == {"user_id", "email", "kind", "since",
                                                    "job_state"}  # fmt: skip
    hashes = superuser.execute(
        text("SELECT request_hash FROM jobs UNION ALL SELECT request_hash FROM external_operations")
    ).scalars()
    keys = superuser.execute(text("SELECT idempotency_key FROM jobs")).scalars()
    needles = [*plant.values(), actor.token, *keys, *(bytes(h).hex() for h in hashes)]
    assert not [n for n in needles if n in response.text]
    wider = await actor.get(f"{JOBS}?days=30")
    assert ("design_draft", "internal", 1) in [
        (f["kind"], f["category"], f["count"]) for f in wider.json()["failures"]
    ]
    assert audit(superuser, actor.user_id) == [("job.list", "ok", None)] * 2
    params = (
        superuser.execute(
            text("SELECT params FROM admin_audit WHERE action = 'job.list' ORDER BY created_at")
        )
        .scalars()
        .all()
    )
    assert params == [{"days": 7}, {"days": 30}]


@pytest.mark.parametrize("days", ["0", "91", "x"])
async def test_a_bad_window_is_422_and_reads_nothing(
    env: Env, make_actor: Any, superuser: Connection, days: str
) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await actor.get(f"{JOBS}?days={days}")
    assert response.status_code == 422, response.text
    assert audit(superuser, actor.user_id) == []


async def test_an_empty_queue_answers_empty_lists(env: Env, make_actor: Any) -> None:
    actor = await stepped_admin(env, make_actor)
    body = (await actor.get(JOBS)).json()
    assert body == {"days": 7, "states": [], "waiting_approvals": [], "failures": [],
                    "uncertain_operations": []}  # fmt: skip
