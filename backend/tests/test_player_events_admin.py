"""The operator's paths write the player events on ``classbeat_maint`` (ADR 0034 section 2,
#1058): an erase writes ``account_deleted`` and a disable ``sessions_revoked`` in their own
transaction, through maint's column grants; the Jobs view lists the failed events without their
subject or ``jti``."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from adminviews import stepped_admin
from authtools import Env
from classbeat.auth.oidc.subject_calc import pairwise_subject
from oidctools import CLIENT_ID, PAIRWISE_KEY, oidc_env
from playertools import GRANT

USERS = "/api/admin/users"


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str, kek_dir: Path) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with oidc_env(
        app_database_url,
        kek_dir,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as env:
        yield env


def events(conn: Connection) -> list[tuple[str, str]]:
    return [
        (r.kind, r.sub)
        for r in conn.execute(text("SELECT kind, sub FROM player_events ORDER BY created_at"))
    ]


async def test_the_operators_erase_writes_account_deleted_and_it_outlives_the_user(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("rider@example.com")
    await env.run(GRANT, u=target)
    done = await actor.call("DELETE", f"{USERS}/{target}", {"email": "rider@example.com"})
    assert done.status_code == 204, done.text
    assert (
        superuser.execute(
            text("SELECT count(*) FROM users WHERE id = :u"), {"u": target}
        ).scalar_one()
        == 0
    )
    assert events(superuser) == [
        ("account_deleted", pairwise_subject(PAIRWISE_KEY, CLIENT_ID, target))
    ]


async def test_the_operators_disable_writes_sessions_revoked(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("rider@example.com")
    await env.run(GRANT, u=target)
    disabled = await actor.post(f"{USERS}/{target}/disable")
    assert disabled.status_code == 200, disabled.text
    assert events(superuser) == [
        ("sessions_revoked", pairwise_subject(PAIRWISE_KEY, CLIENT_ID, target))
    ]


async def test_a_refused_erase_writes_no_event(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("rider@example.com")
    await env.run(GRANT, u=target)
    wrong = await actor.call("DELETE", f"{USERS}/{target}", {"email": "other@example.com"})
    assert wrong.status_code == 409
    assert events(superuser) == []


async def test_the_jobs_view_lists_failed_events_without_their_subject(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    subject, jti = "s" * 43, "j" * 43
    superuser.execute(
        text(
            "INSERT INTO player_events (kind, sub, jti, state, attempt, last_error, created_at, "
            "give_up_at) VALUES ('account_deleted', :s, :j, 'failed', 12, 'status_5xx', "
            "now() - interval '8 days', now() - interval '1 day')"
        ),
        {"s": subject, "j": jti},
    )
    answer = await actor.get("/api/admin/jobs")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    [event] = body["failed_player_events"]
    assert set(event) == {"kind", "created_at", "give_up_at", "attempts", "last_error"}
    assert (event["kind"], event["attempts"], event["last_error"]) == (
        "account_deleted", 12, "status_5xx"
    )  # fmt: skip
    assert body["player_events_truncated"] is False
    assert subject not in answer.text
    assert jti not in answer.text
