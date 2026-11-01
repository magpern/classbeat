"""The data export (ADR 0014 section 2; #337): the user's own rows and nothing else, no secret."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from accounttools import AGENT, Person, person
from classbeat.account.export import build_export
from classbeat.account.registry import SECTIONS
from ctx import make_context
from jobapi import session_cookie
from songtools import SongEnv

pytestmark = pytest.mark.usefixtures("songenv")

URL = "/api/account/export"
# Names of columns that hold a secret, a hash or an internal handle: none may appear as a key.
FORBIDDEN_KEYS = {
    "approval_hash",
    "code_hash",
    "content_hash",
    "dek_nonce",
    "device_hash",
    "granted_by",
    "idempotency_key",
    "key_id",
    "last_hash",
    "lease_version",
    "leased_by",
    "nonce",
    "password_hash",
    "pkce_verifier",
    "refunded_by",
    "request_hash",
    "revoked_by",
    "secret",
    "secret_ciphertext",
    "secret_nonce",
    "state_hash",
    "token_hash",
    "verifier",
    "wrapped_dek",
}


def keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for name, item in value.items():
            yield name
            yield from keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from keys(item)


async def export(who: Person) -> Any:
    return await who.rider.browser.post(URL)


async def test_the_export_holds_my_rows_and_none_of_the_other_users(songenv: SongEnv) -> None:
    mine, other = await person(songenv), await person(songenv)
    answer = await export(mine)
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"].startswith("application/json")
    assert answer.headers["content-disposition"].startswith(
        'attachment; filename="classbeat-export-'
    )
    assert answer.headers["cache-control"] == "no-store"
    document = answer.json()
    assert (document["format"], document["version"]) == ("classbeat-export", 1)
    assert document["account"]["id"] == str(mine.user_id)
    assert document["account"]["email"] == mine.email
    for section in SECTIONS:
        assert section.key in document
    for key in ("classes", "class_revisions", "jobs", "job_events", "external_operations",
                "class_playlists", "spotify_pool", "agent_runs"):  # fmt: skip
        assert document[key], key
    assert {row["id"] for row in document["classes"]} == {str(mine.found.class_id)}, "only my class"
    assert {row["owner_id"] for key in (s.key for s in SECTIONS) for row in document[key]} <= {
        str(mine.user_id)
    }
    # The other user's ids, address and address-bound data appear nowhere in the file.
    for needle in (str(other.user_id), other.email, str(other.found.class_id),
                   str(other.found.job_id), other.ip, f"sub-{other.user_id}"):  # fmt: skip
        assert needle not in answer.text, needle
    # The sign-in side shows what I did myself.
    assert [m["provider"] for m in document["sign_in_methods"]] == ["github"]
    assert document["two_factor"]["enabled"] is True
    assert document["two_factor"]["unused_recovery_codes"] == 1
    assert any(s["ip"] == mine.ip and s["user_agent"] == AGENT for s in document["sessions"])
    assert [e["kind"] for e in document["security_events"]] == ["sign_in"]
    assert document["security_events"][0]["detail"] == {"email": mine.email}


async def test_connections_are_status_only_and_no_secret_or_hash_is_exported(
    songenv: SongEnv,
) -> None:
    mine = await person(songenv)
    songenv.sql(
        "UPDATE users SET password_hash = 'argon2-secret-hash' WHERE id = :u", u=mine.user_id
    )
    answer = await export(mine)
    document = answer.json()
    assert {c["provider"]: c["status"] for c in document["connections"]} == {
        "mowl": "connected",
        "spotify": "connected",
        "swedebeat": "not_connected",
    }
    assert set(keys(document)).isdisjoint(FORBIDDEN_KEYS)
    assert document["account"]["has_password"] is True
    assert "argon2-secret-hash" not in answer.text
    rows = songenv.sql(
        "SELECT secret, wrapped_dek, public FROM connections WHERE owner_id = :u", u=mine.user_id
    ).all()
    assert rows
    for secret, wrapped, _public in rows:
        for blob in (secret, wrapped):
            assert bytes(blob).hex() not in answer.text
    assert "client_id" not in answer.text


async def test_the_export_filters_on_the_owner_even_without_row_level_security(
    songenv: SongEnv, database_url: str
) -> None:
    """Run on the superuser (RLS does not apply): only the application's own filter is left."""
    mine, other = await person(songenv), await person(songenv)
    engine = create_async_engine(database_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        document = await build_export(factory, make_context(mine.user_id))
    finally:
        await engine.dispose()
    assert document is not None
    text = json.dumps(document)
    assert str(mine.found.class_id) in text
    for needle in (str(other.user_id), other.email, str(other.found.class_id)):
        assert needle not in text


async def test_the_export_needs_a_session_recent_authentication_and_the_step_up(
    songenv: SongEnv,
) -> None:
    mine = await person(songenv)
    assert (await songenv.spot.env.client.post(URL)).status_code in (401, 403)
    songenv.sql("UPDATE sessions SET stepup_at = NULL")
    refused = await export(mine)  # a confirmed TOTP and no step-up
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (403, "stepup_required")
    songenv.sql("UPDATE sessions SET stepup_at = now()")
    assert (await export(mine)).status_code == 200
    songenv.sql("UPDATE sessions SET reauth_at = now() - interval '11 minutes'")
    refused = await export(mine)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (403, "reauth_required")


async def test_the_export_is_limited_per_user_and_hour(songenv: SongEnv) -> None:
    mine = await person(songenv)
    songenv.sql("UPDATE sessions SET stepup_at = now()")
    assert [(await export(mine)).status_code for _ in range(5)] == [200] * 5
    assert (await export(mine)).status_code == 429


async def test_a_cross_site_request_cannot_use_the_session_to_burn_the_allowance(
    songenv: SongEnv,
) -> None:
    """The export is a POST behind the CSRF and origin checks: a link, an image or a form on
    another site sends the cookie but no token, and the count of exports stays at zero."""
    mine = await person(songenv)
    client, cookie = mine.rider.browser.client, session_cookie(mine.rider.browser.token)
    for _ in range(8):
        assert (await client.get(URL, headers=cookie)).status_code == 405
        assert (await client.post(URL, headers=cookie)).status_code == 403  # no CSRF token
        evil = cookie | {"Origin": "https://evil.example"}
        assert (await client.post(URL, headers=evil)).status_code == 403
    client.cookies.clear()
    assert [(await export(mine)).status_code for _ in range(5)] == [200] * 5


async def test_the_export_holds_the_dismissed_rules_of_my_class(songenv: SongEnv) -> None:
    """ADR 0017 section 4: ``classes.dismissed_rules`` is exported with the class (the registry
    omits nothing from ``classes``) and goes with the class and the account (cascades)."""
    mine = await person(songenv)
    songenv.sql(
        "UPDATE classes SET dismissed_rules = CAST(:v AS jsonb) WHERE id = :c",
        v='["warmup_short"]',
        c=mine.found.class_id,
    )
    document = (await export(mine)).json()
    assert [c["dismissed_rules"] for c in document["classes"]] == [["warmup_short"]]
    assert next(s for s in SECTIONS if s.table == "classes").omit == frozenset()
