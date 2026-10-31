"""M6 security suite, ciphertext-only database dump (roadmap M6, issue #339; ADR 0004 sections 2
and 7, T41). Fakes only, no network.

After the full fake flows of ``m6world`` (MOWL and Spotify connected, TOTP enrolled, jobs, saves,
an admin session, a password reset, an OAuth sign-up) the data of every table is read the way
``pg_dump --data-only`` writes it (``COPY ... TO STDOUT`` as the superuser, so no row security;
and ``pg_dump`` itself when a client of the server's major version is installed). It holds the
connection rows as ciphertext, and none of the secrets the flows made or used, plain or as
base64 or hex, URL-encoded: no provider token, key, password, session token, CSRF token, mail
token, OAuth code or state, TOTP key or recovery code. The few values stored on purpose in the
clear are named in ``STORED_ON_PURPOSE`` with the reason; anything else found fails the test.
"""

import asyncio
import json

import pytest
from sqlalchemy.engine import Connection, make_url

from m6world import Known, World, capture, full_flows, leaks, spot, world
from spotifytools import spotify_admin
from test_connections import dump_database

__all__ = ["spot", "spotify_admin", "world"]  # fixtures

# Secrets that are in the database in the clear, by design, and why. Empty means every secret of
# the flows is hashed, encrypted or absent.
STORED_ON_PURPOSE: dict[str, str] = {}


async def dump(world: World, admin: Connection, monkeypatch: pytest.MonkeyPatch, url: str):
    known = Known()
    with capture(monkeypatch):
        ids = await full_flows(world, admin, known)
    dsn = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    tables, copied, dumped = await asyncio.to_thread(dump_database, dsn)
    return known, ids, tables, copied, dumped


async def test_a_full_dump_after_the_flows_holds_ciphertext_only(
    world: World, spotify_admin: Connection, monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    known, _, tables, copied, dumped = await dump(world, spotify_admin, monkeypatch, database_url)
    assert {"connections", "sessions", "users", "mfa_totp", "jobs", "class_revisions"} <= set(
        tables
    )
    assert len(known.values) >= 25
    for output in (copied, dumped or b""):
        found = [n for n in leaks(output, known) if n not in STORED_ON_PURPOSE]
        assert found == []


async def test_the_connection_rows_are_ciphertext_with_the_adr_shape(
    world: World, spotify_admin: Connection, monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    from sqlalchemy import text

    known, ids, _, _, _ = await dump(world, spotify_admin, monkeypatch, database_url)
    rows = spotify_admin.execute(
        text("SELECT provider, status, public::text AS public, secret, secret_nonce, "
             "wrapped_dek, dek_nonce, key_id FROM connections WHERE owner_id = :o "
             "ORDER BY provider"),
        {"o": ids["rider"]},
    ).all()  # fmt: skip
    assert [r.provider for r in rows] == ["mowl", "spotify", "swedebeat"]
    for row in rows:
        assert row.status == "connected"
        assert (len(row.secret_nonce), len(row.wrapped_dek), len(row.dek_nonce)) == (12, 48, 12)
        assert len(bytes(row.secret)) > 16
        assert row.key_id == "kek-1"
        # Nothing secret in the one column kept in the clear either.
        assert leaks(row.public, known) == []
    assert spotify_admin.execute(text("SELECT count(*) FROM mfa_totp")).scalar_one() >= 1


async def test_the_database_dump_search_would_see_a_plaintext_token(
    world: World, spotify_admin: Connection, monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """The check is live: a secret written to a column in the clear is found in the dump, in each
    of the forms searched for (as is, URL-encoded, base64, URL-safe base64, hex, upper-case hex)."""
    from sqlalchemy import text

    known, ids, _, _, _ = await dump(world, spotify_admin, monkeypatch, database_url)
    dsn = make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    for name in ("mowl_token", "password", "swedebeat_token"):
        forms = set(known.forms({name})[name])
        assert len(forms) >= 4, name  # raw, base64, hex, upper-case hex (the rest equal for most)
        for form in sorted(forms):
            spotify_admin.execute(
                text("UPDATE connections SET public = CAST(:p AS jsonb) WHERE owner_id = :o "
                     "AND provider = 'mowl'"),
                {"p": json.dumps({"note": form}), "o": ids["rider"]},
            )  # fmt: skip
            _, copied, _ = await asyncio.to_thread(dump_database, dsn)
            assert name in leaks(copied, known), (name, form)
