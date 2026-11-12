"""Requesting an invite (ADR 0033 section 2): ``POST /api/auth/invite-request``, against the
database.

One answer for a new request, a second one while the first is pending, one past the cap and an
address that already has an account (no enumeration); the address is normalized and stored once;
the buckets per address and per IP; only the ``invite`` mode takes requests; the bounds of the
body; the app role cannot read what a visitor wrote; no log line names the address or the text.
Turnstile on this route is covered by ``test_turnstile_routes.py`` (its ``TURNSTILE`` table).
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import ProgrammingError

from authtools import ORIGIN, Env, running_env
from classbeat.auth import invite_request_routes
from flows import Browser, reason

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")  # #1012

PATH = "/api/auth/invite-request"
EMAIL = "Rider.One@Example.com"
HEARD = "A friend at the gym told me about classbeat"
RECEIVED = {"status": "received"}


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    # The test client's peer is a trusted proxy, so ``X-Forwarded-For`` names the client.
    async with running_env(app_database_url, trusted_proxies="127.0.0.1/32") as env:
        yield env


def body(**overrides: Any) -> dict[str, Any]:
    return {"email": EMAIL, "heard_from": HEARD, "instructor": True, "locale": "sv", **overrides}


async def ask(env: Env, ip: str = "198.51.100.1", **overrides: Any) -> Any:
    """``Browser.post`` with ``X-Forwarded-For`` naming the client (the peer is trusted)."""
    browser = Browser(env)
    token = await browser.csrf()
    headers = {"Origin": ORIGIN, "X-CSRF-Token": token, "X-Forwarded-For": ip}
    headers.update(browser._cookie_header())  # pyright: ignore[reportPrivateUsage]
    env.client.cookies.clear()
    return await env.client.post(PATH, json=body(**overrides), headers=headers)


def requests(conn: Connection) -> list[Any]:
    return list(
        conn.execute(
            text(
                "SELECT email, email_normalized, heard_from, instructor, locale, state "
                "FROM invite_requests ORDER BY created_at"
            )
        )
    )


async def test_a_request_is_stored_normalized_and_answered_received(
    env: Env, superuser: Connection
) -> None:
    response = await ask(env, heard_from="  A friend \n at the\tgym  ")
    assert (response.status_code, response.json()) == (202, RECEIVED)
    [row] = requests(superuser)
    assert tuple(row) == (
        EMAIL,
        "rider.one@example.com",
        "A friend at the gym",
        True,
        "sv",
        "pending",
    )


async def test_a_second_request_for_the_address_is_one_row_and_the_same_answer(
    env: Env, superuser: Connection
) -> None:
    first = await ask(env)
    again = await ask(env, email="rider.one@EXAMPLE.com", heard_from="other", instructor=False)
    assert (first.status_code, first.json()) == (again.status_code, again.json()) == (202, RECEIVED)
    [row] = requests(superuser)
    assert (row.heard_from, row.instructor) == (HEARD, True)  # the first one stays
    # Once decided, the address may ask again.
    superuser.execute(text("UPDATE invite_requests SET state = 'declined', decided_at = now()"))
    assert (await ask(env)).status_code == 202
    assert [r.state for r in requests(superuser)] == ["declined", "pending"]


async def test_an_address_with_an_account_gets_the_same_answer_and_no_mail(
    env: Env, superuser: Connection
) -> None:
    await env.user(EMAIL)
    sent: list[Any] = []

    class Spy:
        configured = True

        async def send(self, message: Any) -> None:
            sent.append(message)

    env.app.state.mailer = Spy()
    response = await ask(env)
    assert (response.status_code, response.json()) == (202, RECEIVED)
    assert len(requests(superuser)) == 1  # the admin sees has_account; the visitor learns nothing
    assert sent == []


async def test_past_the_cap_a_request_is_dropped_with_the_same_answer(
    env: Env, superuser: Connection, monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    monkeypatch.setattr(invite_request_routes, "PENDING_CAP", 2)
    for n in range(3):
        response = await ask(env, email=f"rider{n}@example.com", ip=f"198.51.100.{n}")
        assert (response.status_code, response.json()) == (202, RECEIVED)
    assert len(requests(superuser)) == 2
    assert "2 requests are pending" in caplog.text
    assert "rider2" not in caplog.text


async def test_requests_are_limited_per_address_and_per_ip(env: Env) -> None:
    # Per address: 3 an hour, whatever the IP.
    for n in range(3):
        assert (await ask(env, ip=f"198.51.100.{n}")).status_code == 202
    limited = await ask(env, ip="198.51.100.9")
    assert limited.status_code == 429
    assert "retry-after" in limited.headers
    # Per IP: 5 an hour, whatever the address.
    for n in range(5):
        assert (await ask(env, email=f"x{n}@example.com", ip="203.0.113.7")).status_code == 202
    assert (await ask(env, email="x9@example.com", ip="203.0.113.7")).status_code == 429


@pytest.mark.parametrize(
    "settings",
    [
        {"signup_mode": "closed"},
        # Public sign-up (ADR 0032 section 7) is the only way to ``open``: no invite is needed.
        {"public_signup": True},
        {"public_signup": True, "signup_mode": "open"},
    ],
)
async def test_only_the_invite_mode_takes_requests(
    clean_auth: None, app_database_url: str, superuser: Connection, settings: dict[str, Any]
) -> None:
    async with running_env(app_database_url, **settings) as env:
        response = await ask(env)
    assert (response.status_code, reason(response)) == (403, "requests_closed")
    assert requests(superuser) == []


async def test_the_database_setting_can_close_requests(env: Env, superuser: Connection) -> None:
    superuser.execute(
        text("INSERT INTO app_settings (key, value) VALUES ('signup_mode', '\"closed\"')")
    )
    try:
        response = await ask(env)
    finally:
        superuser.execute(text("DELETE FROM app_settings WHERE key = 'signup_mode'"))
    assert (response.status_code, reason(response)) == (403, "requests_closed")


@pytest.mark.parametrize(
    "overrides",
    [
        {"heard_from": "x" * 301},
        {"heard_from": "x" * 1201},
        {"heard_from": "bidi \u202e override"},
        {"heard_from": "zero\u200bwidth"},
        {"heard_from": "nul \x00"},
        {"instructor": "yes"},
        {"instructor": None},
        {"email": "not an address"},
        {"email": "a@b.c\r\nBcc: x@example.com"},
    ],
)
async def test_bad_bodies_are_422_without_echoing_the_input(
    env: Env, superuser: Connection, overrides: dict[str, Any]
) -> None:
    response = await ask(env, **overrides)
    assert response.status_code == 422
    assert "input" not in response.text
    assert EMAIL not in response.text
    assert requests(superuser) == []


async def test_exactly_300_characters_is_accepted(env: Env, superuser: Connection) -> None:
    response = await ask(env, heard_from="é" * 300)
    assert response.status_code == 202, response.text
    assert requests(superuser)[0].heard_from == "é" * 300


async def test_a_body_over_the_app_cap_is_refused(env: Env) -> None:
    response = await ask(env, heard_from="x" * (70 * 1024))
    assert response.status_code == 413


async def test_no_log_line_names_the_address_or_the_text(env: Env, caplog: Any) -> None:
    caplog.set_level(logging.DEBUG)
    await ask(env)
    await ask(env)
    await ask(env, email="bad")
    assert "rider" not in caplog.text.lower()
    assert "gym" not in caplog.text


def test_the_app_role_cannot_read_the_address_or_the_text(
    superuser: Connection, app_database_url: str
) -> None:
    superuser.execute(
        text(
            "INSERT INTO invite_requests (email, email_normalized, heard_from, instructor) "
            "VALUES ('a@example.com', 'a@example.com', 'secret text', false)"
        )
    )
    engine = create_engine(app_database_url)
    try:
        with engine.connect() as conn:
            for column in ("email", "email_normalized", "heard_from", "instructor", "locale", "*"):
                with pytest.raises(ProgrammingError, match="permission denied"):
                    conn.execute(text(f"SELECT {column} FROM invite_requests"))  # noqa: S608
                conn.rollback()
            assert conn.execute(text("SELECT state FROM invite_requests")).scalar_one() == "pending"
            with pytest.raises(ProgrammingError, match="permission denied"):
                conn.execute(text("UPDATE invite_requests SET state = 'issued'"))
    finally:
        engine.dispose()
