"""Retention of invite requests (ADR 0033 section 6): the hourly purge deletes a decided request
30 days after the decision and a pending one 90 days after it was made, and keeps the rest; the
invite purge leaves a tier's code in place (``redeem_codes.invite_id`` set to NULL by its foreign
key, which the count guard allows)."""

from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import Env
from classbeat import auth


def add(conn: Connection, email: str, state: str, created: str, decided: str | None) -> None:
    conn.execute(
        text(
            "INSERT INTO invite_requests (email, email_normalized, instructor, state, created_at, "
            "decided_at) VALUES (:e, :e, false, :s, now() - CAST(:c AS interval), "
            "now() - CAST(:d AS interval))"
        ),
        {"e": email, "s": state, "c": created, "d": decided},
    )


async def test_the_purge_keeps_requests_only_as_long_as_the_adr_says(
    env: Env, superuser: Connection
) -> None:
    add(superuser, "old-issued@example.com", "issued", "40 days", "31 days")
    add(superuser, "old-declined@example.com", "declined", "40 days", "31 days")
    add(superuser, "recent-issued@example.com", "issued", "40 days", "29 days")
    add(superuser, "old-pending@example.com", "pending", "91 days", None)
    add(superuser, "pending@example.com", "pending", "89 days", None)
    counts = await auth.purge_expired(env.factory, env.settings)
    assert counts["invite_requests"] == 3
    kept = superuser.execute(text("SELECT email FROM invite_requests ORDER BY email")).scalars()
    assert list(kept) == ["pending@example.com", "recent-issued@example.com"]
    assert (await auth.purge_expired(env.factory, env.settings))["invite_requests"] == 0


async def test_the_invite_purge_keeps_the_tier_code_and_the_request(
    env: Env, superuser: Connection
) -> None:
    invite_id = superuser.execute(
        text(
            "INSERT INTO invites (code_hash, expires_at, used_at) "
            "VALUES ('i', now(), now() - interval '31 days') RETURNING id"
        )
    ).scalar_one()
    superuser.execute(
        text(
            "INSERT INTO redeem_codes (code_hash, label, plan_id, grant_days, max_redemptions, "
            "redeemed_count, expires_at, invite_id) "
            "VALUES (sha256('c'), 'Invite request', 'pro', 30, 1, 1, now(), :i)"
        ),
        {"i": invite_id},
    )
    add(superuser, "r@example.com", "issued", "20 days", "10 days")
    superuser.execute(text("UPDATE invite_requests SET invite_id = :i"), {"i": invite_id})
    counts = await auth.purge_expired(env.factory, env.settings)
    assert counts["invites"] == 1
    assert superuser.execute(text("SELECT invite_id, redeemed_count FROM redeem_codes")).one() == (
        None,
        1,
    )
    assert superuser.execute(text("SELECT invite_id FROM invite_requests")).scalar_one() is None
