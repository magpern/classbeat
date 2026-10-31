"""M5 security suite, follow-up (#227): invites and redeem codes under the #279 (AD2) review's
items, and the format-character check of the #303 review (note 5).

- The invite disable race (Architect decision): a disable sets ``expires_at = created_at``, so a
  sign-up confirm whose transaction began before the disable committed still sees the invite
  expired (``now()`` is the transaction's start); and the consume locks the invite row first, so
  a disable that meets a consume in flight waits for it and answers ``invite_used``.
- Disabling a redeem code no redeem can use any more answers 409 ``already_expired``,
  ``used_up`` or ``already_disabled``, read from the code's locked row, as invites do.
- Labels and grant notes refuse format characters (Unicode ``Cf``: the bidi embeddings,
  overrides and isolates U+202A-202E and U+2066-2069, zero-width characters), the line and
  paragraph separators, and C1 control characters, apart from ``bounded_text``'s check.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Connection, make_url

from authtools import Env, running_env
from classbeat.auth.accounts import use_invite

INVITES = "/api/admin/invites"
CODES = "/api/admin/redeem-codes"


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as env:
        yield env


def reason(response: Any) -> str:
    return response.json()["detail"]["reason"]


async def stepped_admin(env: Env, make_actor: Any) -> Any:
    user_id = await env.user("a@example.com", role="admin")
    actor = await make_actor(user_id, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now() WHERE user_id = :u", u=user_id)
    actor.user_id = user_id
    return actor


def audit(conn: Connection, actor_id: object) -> list[tuple[str, str, str | None]]:
    return [
        tuple(r)
        for r in conn.execute(
            text(
                "SELECT action, outcome, reason FROM admin_audit WHERE actor_id = :a "
                "ORDER BY created_at, id"
            ),
            {"a": actor_id},
        )
    ]


async def wait_for_maint_lock(conn: Connection, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        waiting = conn.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity WHERE usename = 'classbeat_maint' "
                "AND wait_event_type = 'Lock'"
            )
        ).scalar_one()
        if waiting:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the disable never waited for the consume's row lock")


# --- the invite disable race ------------------------------------------------------------------


async def test_a_confirm_that_began_before_a_disable_cannot_use_the_invite(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """Two sessions: the sign-up confirm's transaction starts (its ``now()`` is fixed), the
    admin's disable commits on its own connection, then the confirm consumes. With ``expires_at
    = now()`` the confirm's earlier ``now()`` would still see the invite valid."""
    actor = await stepped_admin(env, make_actor)
    invite_id = uuid.UUID((await actor.post(INVITES, {"expires_days": 7})).json()["invite"]["id"])
    async with env.db() as confirm:  # the confirm's transaction, begun before the disable
        await confirm.execute(text("SELECT now()"))
        await asyncio.sleep(0.05)
        disabled = await actor.post(f"{INVITES}/{invite_id}/disable")
        assert disabled.status_code == 200, disabled.text
        assert await use_invite(confirm, invite_id) is False
    row = superuser.execute(
        text("SELECT used_at, expires_at = created_at FROM invites WHERE id = :i"), {"i": invite_id}
    ).one()
    assert tuple(row) == (None, True)


async def test_a_disable_that_meets_a_consume_in_flight_waits_and_answers_invite_used(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    invite_id = uuid.UUID((await actor.post(INVITES, {"expires_days": 7})).json()["invite"]["id"])
    async with env.db() as confirm:
        assert await use_invite(confirm, invite_id) is True  # locked and marked, not committed
        disable = asyncio.create_task(actor.post(f"{INVITES}/{invite_id}/disable"))
        await wait_for_maint_lock(superuser)
        assert not disable.done()
    response = await disable
    assert (response.status_code, reason(response)) == (409, "invite_used")
    used = superuser.execute(
        text("SELECT used_at IS NOT NULL, expires_at > created_at FROM invites WHERE id = :i"),
        {"i": invite_id},
    ).one()
    assert tuple(used) == (True, True)  # used, and its expiry untouched by the disable


async def test_use_invite_refuses_a_used_an_expired_and_an_unknown_invite(
    env: Env, superuser: Connection
) -> None:
    def invite(expires: str, used: bool = False) -> uuid.UUID:
        return superuser.execute(
            text(
                "INSERT INTO invites (code_hash, expires_at, used_at) VALUES "
                "(sha256(gen_random_uuid()::text::bytea), now() + CAST(:e AS interval), "
                "CASE WHEN :u THEN now() END) RETURNING id"
            ),
            {"e": expires, "u": used},
        ).scalar_one()

    live, used, expired = invite("1 day"), invite("1 day", used=True), invite("-1 second")
    async with env.db() as db:
        assert await use_invite(db, used) is False
        assert await use_invite(db, expired) is False
        assert await use_invite(db, uuid.uuid4()) is False
        assert await use_invite(db, live) is True
        assert await use_invite(db, live) is False  # once


# --- redeem-code disable ----------------------------------------------------------------------


def plant_code(conn: Connection, state: str) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO redeem_codes (code_hash, label, plan_id, grant_days, max_redemptions, "
            "redeemed_count, expires_at, disabled_at) VALUES "
            "(sha256(gen_random_uuid()::text::bytea), :l, 'pro', 30, 2, :n, "
            "now() + CAST(:e AS interval), CASE WHEN :d THEN now() END) RETURNING id"
        ),
        {
            "l": state,
            "n": 2 if state == "used_up" else 0,
            "e": "-1 second" if state == "expired" else "9 days",
            "d": state == "disabled",
        },
    ).scalar_one()


@pytest.mark.parametrize(("state", "status", "expected"), [
    ("active", 200, None),
    ("expired", 409, "already_expired"),
    ("used_up", 409, "used_up"),
    ("disabled", 409, "already_disabled"),
])  # fmt: skip
async def test_a_code_disable_answers_from_the_codes_locked_state(
    env: Env, make_actor: Any, superuser: Connection, state: str, status: int, expected: str | None
) -> None:
    actor = await stepped_admin(env, make_actor)
    code_id = plant_code(superuser, state)
    statements: list[str] = []
    engine = env.app.state.admin_db._maint_engine.sync_engine  # pyright: ignore[reportPrivateUsage]

    def seen(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        if "redeem_codes" in statement:
            statements.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", seen)
    try:
        response = await actor.post(f"{CODES}/{code_id}/disable")
    finally:
        event.remove(engine, "before_cursor_execute", seen)
    assert response.status_code == status, response.text
    assert statements[0].startswith("SELECT")
    assert statements[0].endswith("FOR UPDATE")
    if status == 409:
        assert reason(response) == expected
        assert len(statements) == 1, statements  # no UPDATE tried, no second read
        assert audit(superuser, actor.user_id) == [("code.disable", "refused", expected)]
    else:
        assert response.json()["state"] == "disabled"
        assert audit(superuser, actor.user_id) == [("code.disable", "ok", None)]
    disabled_count = superuser.execute(
        text("SELECT count(*) FROM redeem_codes WHERE id = :i AND disabled_at IS NOT NULL"),
        {"i": code_id},
    ).scalar_one()
    assert disabled_count == (0 if state in ("expired", "used_up") else 1)


# --- format characters in labels and notes ----------------------------------------------------

REFUSED_TEXT = [
    "Spring \u202egnp.exe",  # right-to-left override
    "Spring \u202a",  # left-to-right embedding
    "Spring \u2066x\u2069",  # left-to-right isolate, pop directional isolate
    "Spr\u200bing",  # zero-width space
    "Spr\u200ding",  # zero-width joiner
    "Spr\u00ading",  # soft hyphen
    "\ufeffSpring",  # byte-order mark
    "Spring\u2028line",  # line separator
    "Spring\u2029para",  # paragraph separator
    "Spr\x85ing",  # NEL, a C1 control character (a trailing one is stripped as whitespace)
    "Spr\x9bing",  # CSI, a C1 control character
]
ACCEPTED_TEXT = [
    "V\u00e5r grupp",
    "\u00dcn\u00efc\u00f6d\u00e9",
    "Spin \U0001f6b4 club",
    "\u65e5\u672c\u8a9e",
]


def code_body(label: str) -> dict[str, Any]:
    return {
        "label": label, "plan_id": "pro", "features": [], "grant_days": 30,
        "max_redemptions": 5, "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
    }  # fmt: skip


@pytest.mark.parametrize("label", REFUSED_TEXT)
async def test_a_label_with_a_format_or_control_character_is_refused(
    env: Env, make_actor: Any, superuser: Connection, label: str
) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await actor.post(CODES, code_body(label))
    assert response.status_code == 422, response.text
    assert audit(superuser, actor.user_id) == [("admin.request", "refused", "invalid_input")]
    assert superuser.execute(text("SELECT count(*) FROM redeem_codes")).scalar_one() == 0


@pytest.mark.parametrize("note", REFUSED_TEXT)
async def test_a_grant_note_with_a_format_or_control_character_is_refused(
    env: Env, make_actor: Any, superuser: Connection, note: str
) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("t@example.com")
    response = await actor.post(
        f"/api/admin/users/{target}/entitlements", {"plan_id": "pro", "note": note}
    )
    assert response.status_code == 422, response.text
    assert superuser.execute(text("SELECT count(*) FROM entitlements")).scalar_one() == 0


async def test_ordinary_unicode_labels_and_notes_are_kept(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("t@example.com")
    for text_ in ACCEPTED_TEXT:
        made = await actor.post(CODES, code_body(text_))
        assert made.status_code == 201, made.text
        assert made.json()["redeem_code"]["label"] == text_
        granted = await actor.post(
            f"/api/admin/users/{target}/entitlements", {"plan_id": "pro", "note": text_}
        )
        assert granted.status_code == 201, granted.text
    labels = superuser.execute(text("SELECT label FROM redeem_codes ORDER BY label")).scalars()
    assert sorted(labels) == sorted(ACCEPTED_TEXT)
