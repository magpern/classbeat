"""The admin's bulk decline and the pending count of the invite requests (ADR 0033 section 3,
#989), end to end against the database, and the two facts the screen reads from Settings.

The bulk decline declines each listed pending request in its own audited transaction (one
``invite_request.decline`` row per declined request, its id as the target and no params), skips
one that is gone or decided meanwhile without undoing the others, records the skips of one call in
one ``invite_request.decline_skipped`` row (a count, no ids, #994) that spends one slot of the
refusal limiter however many ids were stale, and refuses an empty, oversized or foreign body
whole. The list counts the whole pending queue whatever its filter and page.
Settings says whether mail is configured (a boolean) and how many invites issued with a tier are
still unused. The gate on the bulk route is in ``test_admin_invite_requests.py``.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import Env
from test_admin_invite_requests import (
    PATH,
    TIER,
    add_request,
    env,
    stepped_admin,
)

__all__ = ["env"]  # fixtures

BULK = f"{PATH}/decline"


def states(conn: Connection) -> dict[uuid.UUID, str]:
    return {r.id: r.state for r in conn.execute(text("SELECT id, state FROM invite_requests"))}


def skipped_rows(conn: Connection) -> list[tuple[str, str | None, str | None, str | None, Any]]:
    return [
        (r.outcome, r.reason, r.target_kind, r.target_id, r.params)
        for r in conn.execute(
            text(
                "SELECT outcome, reason, target_kind, target_id, params FROM admin_audit "
                "WHERE action = 'invite_request.decline_skipped' ORDER BY created_at, id"
            )
        )
    ]


def decline_rows(conn: Connection) -> list[tuple[str, str | None, str | None, Any]]:
    return [
        (r.outcome, r.reason, r.target_id, r.params)
        for r in conn.execute(
            text(
                "SELECT outcome, reason, target_id, params FROM admin_audit "
                "WHERE action = 'invite_request.decline' AND target_kind = 'invite_request' "
                "ORDER BY created_at, id"
            )
        )
    ]


async def test_the_bulk_decline_declines_each_pending_request_with_its_own_audit_row(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    one = add_request(superuser, "one@example.com")
    two = add_request(superuser, "two@example.com")
    decided = add_request(superuser, "decided@example.com")
    kept = add_request(superuser, "kept@example.com")
    assert (await actor.post(f"{PATH}/{decided}/decline")).status_code == 200
    gone = uuid.uuid4()
    ids = [str(one), str(decided), str(gone), str(two), str(one)]
    response = await actor.post(BULK, {"ids": ids})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "declined": [str(one), str(two)],
        "skipped": [str(decided), str(gone)],
    }
    assert states(superuser) == {
        one: "declined",
        two: "declined",
        decided: "declined",
        kept: "pending",
    }
    decided_by = superuser.execute(
        text("SELECT DISTINCT decided_by FROM invite_requests WHERE state = 'declined'")
    ).all()
    assert [r[0] for r in decided_by] == [actor.user_id]
    # One row per declined request, the repeated id once; the stale ones share one summary row
    # with their count only (no id); no address, no text anywhere.
    assert decline_rows(superuser) == [
        ("ok", None, str(decided), {}),
        ("ok", None, str(one), {}),
        ("ok", None, str(two), {}),
    ]
    summary = [("refused", "already_decided_or_missing", "invite_request", None, {"skipped": 2})]
    assert skipped_rows(superuser) == summary
    logged = superuser.execute(text("SELECT params::text FROM admin_audit")).scalars().all()
    assert not any("example.com" in p or str(gone) in p or str(decided) in p for p in logged)


async def test_a_bulk_decline_with_many_stale_ids_writes_one_row_and_spends_one_refusal_slot(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """More stale ids than the limiter's 20 per admin: one summary row, nothing lost, and the
    admin's refusal budget is still there for the next refusal (#994)."""
    actor = await stepped_admin(env, make_actor)
    limiter = env.app.state.admin_db.limiter
    pending = add_request(superuser)
    stale = [str(uuid.uuid4()) for _ in range(limiter.per_admin + 10)]
    response = await actor.post(BULK, {"ids": [*stale, str(pending)]})
    assert response.status_code == 200, response.text
    assert response.json() == {"declined": [str(pending)], "skipped": stale}
    assert decline_rows(superuser) == [("ok", None, str(pending), {})]
    summary = (
        "refused",
        "already_decided_or_missing",
        "invite_request",
        None,
        {"skipped": len(stale)},
    )
    assert skipped_rows(superuser) == [summary]
    assert limiter._counts[actor.user_id] == 1  # pyright: ignore[reportPrivateUsage]
    # A second such call is one more row, and a single refusal is still audited after both.
    assert (await actor.post(BULK, {"ids": stale})).status_code == 200
    assert len(skipped_rows(superuser)) == 2
    assert (await actor.post(f"{PATH}/{pending}/decline")).status_code == 409
    assert decline_rows(superuser)[-1] == ("refused", "already_decided", str(pending), {})


async def test_a_bulk_decline_with_nothing_stale_writes_no_summary_row(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    ids = [add_request(superuser, f"f{n}@example.com") for n in range(2)]
    response = await actor.post(BULK, {"ids": [str(i) for i in ids]})
    assert response.json()["skipped"] == []
    assert skipped_rows(superuser) == []
    assert [row[0] for row in decline_rows(superuser)] == ["ok", "ok"]


async def test_the_bulk_decline_refuses_a_bad_body_whole(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    request_id = add_request(superuser)
    for body in (
        {"ids": []},
        {"ids": [str(uuid.uuid4()) for _ in range(100)] + [str(request_id)]},
        {"ids": [str(request_id)], "email": "x@example.com"},
        {"ids": ["not-an-id"]},
        {},
    ):
        assert (await actor.post(BULK, body)).status_code == 422, body
    assert states(superuser) == {request_id: "pending"}
    assert decline_rows(superuser) == []
    # Exactly the bound is accepted.
    many = [str(uuid.uuid4()) for _ in range(99)] + [str(request_id)]
    answer = (await actor.post(BULK, {"ids": many})).json()
    assert (answer["declined"], len(answer["skipped"])) == ([str(request_id)], 99)
    assert [row[-1] for row in skipped_rows(superuser)] == [{"skipped": 99}]


async def test_the_list_counts_the_whole_pending_queue_on_every_page_and_filter(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    ids = [add_request(superuser, f"p{n}@example.com") for n in range(3)]
    assert (await actor.post(f"{PATH}/{ids[0]}/decline")).status_code == 200
    first = (await actor.get(f"{PATH}?state=pending&limit=1")).json()
    assert (len(first["requests"]), first["next_offset"], first["pending"]) == (1, 1, 2)
    last = (await actor.get(f"{PATH}?state=pending&offset=1&limit=1")).json()
    assert (len(last["requests"]), last["next_offset"], last["pending"]) == (1, None, 2)
    declined = (await actor.get(f"{PATH}?state=declined")).json()
    assert (len(declined["requests"]), declined["pending"]) == (1, 2)


async def test_settings_counts_the_unused_invites_issued_with_a_tier(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)

    async def facts() -> tuple[bool, int]:
        read_only = (await actor.get("/api/admin/settings")).json()["read_only"]
        return read_only["mail_configured"], read_only["unused_tier_invites"]

    assert await facts() == (True, 0)  # this env has the SMTP fake
    tiered = [add_request(superuser, f"t{n}@example.com") for n in range(3)]
    for request_id in tiered:
        assert (await actor.post(f"{PATH}/{request_id}/approve", TIER)).status_code == 201
    plain = add_request(superuser, "plain@example.com")
    assert (await actor.post(f"{PATH}/{plain}/approve", {})).status_code == 201
    assert await facts() == (True, 3)  # a plain invite carries no tier
    invite_of = text("SELECT invite_id FROM invite_requests WHERE id = :i")
    used, expired = (superuser.execute(invite_of, {"i": i}).scalar_one() for i in tiered[:2])
    superuser.execute(text("UPDATE invites SET used_at = now() WHERE id = :i"), {"i": used})
    superuser.execute(
        text("UPDATE invites SET expires_at = now() - interval '1 second' WHERE id = :i"),
        {"i": expired},
    )
    assert await facts() == (True, 1)


async def test_settings_says_when_mail_is_not_configured(
    env: Env, make_actor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await stepped_admin(env, make_actor)
    quiet = env.settings.model_copy(update={"smtp_host": None})
    monkeypatch.setattr(env.app.state, "settings", quiet)
    read_only = (await actor.get("/api/admin/settings")).json()["read_only"]
    assert read_only["mail_configured"] is False
    assert not any("smtp" in key or "host" in key for key in read_only)  # never an SMTP detail
