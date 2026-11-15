"""The operator's paths write the player events on ``classbeat_maint`` (ADR 0034 section 2,
#1058): an erase writes ``account_deleted`` and a disable ``sessions_revoked`` in their own
transaction, through maint's column grants. ``GET /api/admin/player-events`` lists the failed
events with their subject for the operator, never a ``jti`` or a pending row, and every read is
an audit row of its own (``player.event.list``); the Jobs view itself carries no subject."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from adminviews import audit, reason, stepped_admin
from authtools import Env
from classbeat.admin.jobs import FailedPlayerEvent
from classbeat.auth.oidc.subject_calc import pairwise_subject
from oidctools import CLIENT_ID, PAIRWISE_KEY, oidc_env
from playereventtools import GRANT

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


FAILED = (
    "INSERT INTO player_events (kind, sub, jti, state, attempt, last_error, created_at, "
    "give_up_at) VALUES (:k, :s, :j, :state, 12, 'status_5xx', "
    "now() - interval '8 days', now() - interval '1 day')"
)


async def test_the_failed_events_are_listed_with_their_subject_and_audited(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    failed, pending = ("s" * 43, "j" * 43), ("p" * 43, "q" * 43)
    superuser.execute(
        text(FAILED), {"k": "account_deleted", "s": failed[0], "j": failed[1], "state": "failed"}
    )
    superuser.execute(
        text(FAILED),
        {"k": "sessions_revoked", "s": pending[0], "j": pending[1], "state": "pending"},
    )
    answer = await actor.get("/api/admin/player-events")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["truncated"] is False
    [event] = body["events"]  # the failed row only, never a pending one
    assert set(event) == {"kind", "sub", "created_at", "give_up_at", "attempts", "last_error"}
    assert (event["kind"], event["sub"], event["attempts"], event["last_error"]) == (
        "account_deleted", failed[0], 12, "status_5xx"
    )  # fmt: skip
    assert failed[1] not in answer.text  # never the jti
    assert pending[0] not in answer.text
    assert audit(superuser, actor.user_id)[-1] == ("player.event.list", "ok", None)


def test_the_answer_model_keeps_the_subject_out_of_its_repr() -> None:
    event = FailedPlayerEvent.model_validate(
        {"kind": "account_deleted", "sub": "s" * 43, "created_at": "2026-11-01T00:00:00Z",
         "give_up_at": "2026-11-08T00:00:00Z", "attempts": 3, "last_error": "network"}
    )  # fmt: skip
    assert event.sub == "s" * 43
    assert "s" * 43 not in repr(event)
    assert "s" * 43 not in str(event)


async def test_the_jobs_view_carries_no_subject(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    subject, jti = "s" * 43, "j" * 43
    superuser.execute(
        text(FAILED), {"k": "account_deleted", "s": subject, "j": jti, "state": "failed"}
    )
    answer = await actor.get("/api/admin/jobs")
    assert answer.status_code == 200, answer.text
    assert "failed_player_events" not in answer.json()
    assert subject not in answer.text
    assert jti not in answer.text


async def test_a_user_who_is_not_an_admin_gets_no_subject(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    superuser.execute(
        text(FAILED), {"k": "account_deleted", "s": "s" * 43, "j": "j" * 43, "state": "failed"}
    )
    plain = await make_actor(await env.user("plain@example.com"), mfa=True)
    answer = await plain.get("/api/admin/player-events")
    assert (answer.status_code, reason(answer)) == (403, "forbidden")
    assert "s" * 43 not in answer.text
    assert superuser.execute(text("SELECT count(*) FROM admin_audit")).scalar_one() == 0
