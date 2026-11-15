"""The hourly purge removes every user's expired or used player link flows and keeps the live
ones (ADR 0034 section 3, as ADR 0009 for Spotify's flows)."""

import hashlib

from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import Env
from classbeat import auth


def digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


async def test_the_purge_removes_expired_and_used_flows_of_every_user(
    env: Env, player_admin: Connection
) -> None:
    owners = [await env.user("a@example.com"), await env.user("b@example.com")]
    for owner in owners:
        for name, created, expires, used in (
            ("live", "now()", "now() + interval '10 minutes'", None),
            ("expired", "now() - interval '11 minutes'", "now() - interval '1 minute'", None),
            ("used", "now()", "now() + interval '10 minutes'", "now()"),
        ):
            # Test constants only: the times are SQL expressions.
            statement = (
                "INSERT INTO player_link_flows (owner_id, state_hash, session_hash, verifier, "  # noqa: S608
                "redirect_uri, created_at, expires_at, used_at) VALUES "
                f"(:o, :h, :h, :v, 'https://classbeat.test/cb', {created}, {expires}, "
                f"{used or 'NULL'})"
            )
            player_admin.execute(text(statement), {"o": owner, "h": digest(name), "v": "A" * 43})
    counts = await auth.purge_expired(env.factory, env.settings)
    assert counts["player_link_flows"] == 4
    left = player_admin.execute(text("SELECT owner_id, state_hash FROM player_link_flows")).all()
    assert sorted((r.owner_id, bytes(r.state_hash)) for r in left) == sorted(
        (owner, digest("live")) for owner in owners
    )
