"""Rate limits of MOWL connect (ADR 0004 section 4): 5 per user, 20 per IP and 5 per normalized
MOWL email per 15 minutes, reserved before the sign-in (a refused attempt never reaches MOWL);
a success releases its slots."""

import time
from typing import Any

import pytest
from sqlalchemy import Float, func, literal

from authtools import Env
from classbeat.auth import ratelimit
from mowlfake import EMAIL, PASSWORD, FakeMowl

CONNECT = "/api/connections/mowl"
CONNECT_WINDOW = 15 * 60
pytestmark = pytest.mark.usefixtures("mowl_api", "fixed_connect_window")


@pytest.fixture
def fixed_connect_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold connect buckets in one fixed window for the whole test (#344).

    The limiter keys windows off Postgres ``clock_timestamp()`` (``ratelimit._window_start``).
    When the sixth attempt lands in the next 15-minute window the per-user count resets, the
    route reaches MOWL with a decoy email, and the fake answers 400 (invalid credentials) instead
    of 429 ``rate_limited``."""
    instant = float(int(time.time() // CONNECT_WINDOW) * CONNECT_WINDOW)

    def frozen_window(window: int) -> Any:
        return func.to_timestamp(literal(instant // window * window, Float))

    monkeypatch.setattr(ratelimit, "_window_start", frozen_window)


WRONG = "wrong-password"


def attempt(email: str = EMAIL, password: str = WRONG) -> dict[str, str]:
    return {"email": email, "password": password}


async def test_five_attempts_per_user_then_429_before_mowl(
    env: Env, make_actor: Any, mowl_api: FakeMowl
) -> None:
    actor = await make_actor(await env.user("user@example.com"))
    for n in range(5):
        response = await actor.post(CONNECT, attempt(f"rider{n}@example.com"))
        assert response.status_code == 400
    refused = await actor.post(CONNECT, attempt("rider9@example.com", PASSWORD))
    assert refused.status_code == 429
    assert refused.json() == {"detail": {"reason": "rate_limited"}}
    assert 0 < int(refused.headers["Retry-After"]) <= 15 * 60
    assert len(mowl_api.passwords_seen) == 5  # the sixth never reached MOWL


async def test_a_success_releases_its_slots(env: Env, make_actor: Any) -> None:
    actor = await make_actor(await env.user("user@example.com"))
    for _ in range(4):
        assert (await actor.post(CONNECT, attempt())).status_code == 400
    for _ in range(3):  # each success gives its slot back, so the user is never locked out
        assert (await actor.post(CONNECT, attempt(password=PASSWORD))).status_code == 200
    assert (await actor.post(CONNECT, attempt())).status_code == 400  # the fifth slot
    assert (await actor.post(CONNECT, attempt())).status_code == 429


async def test_five_attempts_per_mowl_email_across_users(
    env: Env, make_actor: Any, mowl_api: FakeMowl
) -> None:
    for n in range(5):
        actor = await make_actor(await env.user(f"user{n}@example.com"))
        assert (await actor.post(CONNECT, attempt(" Rider@Example.com "))).status_code == 400
    other = await make_actor(await env.user("late@example.com"))
    assert (await other.post(CONNECT, attempt("rider@example.com"))).status_code == 429
    assert len(mowl_api.passwords_seen) == 5
    # Another MOWL email from the same new user is still allowed.
    assert (await other.post(CONNECT, attempt("someone@example.com"))).status_code == 400


async def test_twenty_attempts_per_ip(env: Env, make_actor: Any, mowl_api: FakeMowl) -> None:
    for n in range(20):
        actor = await make_actor(await env.user(f"user{n}@example.com"))
        assert (await actor.post(CONNECT, attempt(f"r{n}@example.com"))).status_code == 400
    actor = await make_actor(await env.user("late@example.com"))
    assert (await actor.post(CONNECT, attempt("fresh@example.com"))).status_code == 429
    assert len(mowl_api.passwords_seen) == 20
