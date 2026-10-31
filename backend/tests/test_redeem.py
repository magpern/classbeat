"""Redeem codes (ADR 0011 section 2; PL1, #212): ``classbeat.plans.codes`` and
``POST /api/account/redeem``. One use left and parallel redeems give one entitlement; the user's own
second redeem is ``already_redeemed`` and changes no count; every other bad code is the same 400;
codes never reach a log; overlapping codes don't add up. Fakes only."""

import asyncio
import io
import logging
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.log import JsonFormatter
from classbeat.plans import codes
from jobapi import Browser
from plantools import about, plant_code, redeemed_count


@pytest.fixture
def users_plan() -> None:
    """These tests are about the plan itself: their users start on ``free`` (no grant)."""
    return None


REDEEM = "/api/account/redeem"


# --- codes ------------------------------------------------------------------------------------


def test_a_new_code_round_trips_and_only_its_hash_is_kept() -> None:
    code, digest = codes.new_code()
    assert len(code) == codes.CODE_LENGTH
    assert set(code) <= set(codes.ALPHABET)
    raw = codes.parse(code)
    assert raw is not None
    assert len(raw) == 16
    assert codes.code_hash(raw) == digest == codes.hash_typed(code)
    assert len(digest) == 32
    assert code.encode() not in digest


def test_parsing_is_forgiving_as_crockford_intends_and_strict_otherwise() -> None:
    raw = bytes(range(16))
    code = codes.encode(raw)
    spaced = "-".join(code[i : i + 5] for i in range(0, len(code), 5))
    variants = [code.lower(), spaced, f" {code} ", code.replace("0", "O").replace("1", "I")]
    assert {codes.parse(v) for v in variants} == {raw}
    assert codes.parse(code[:-1]) is None  # too short
    assert codes.parse(code + "0") is None  # too long
    assert codes.parse(code[:-1] + "U") is None  # not in the alphabet
    assert codes.parse("Z" + code[1:]) is None  # above 128 bits
    assert codes.parse(code[:-1] + chr(0x660)) is None  # a non-ASCII digit
    assert codes.parse("") is None


# --- redeem -----------------------------------------------------------------------------------


async def test_a_code_gives_its_plan_and_days_and_shows_on_the_plan(
    admin: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    code, code_id = plant_code(admin, plan="instructor", features=("song_picking",), days=30)
    response = await alice.post(REDEEM, {"code": code.lower()})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["plan"], body["features"]) == ("instructor", ["song_picking"])
    assert about(body["ends_at"], 30)
    assert redeemed_count(admin, code_id) == 1
    row = admin.execute(
        text(
            "SELECT e.source, e.code_id, e.ends_at - e.starts_at AS span, r.code_id AS r_code "
            "FROM entitlements e JOIN code_redemptions r ON r.entitlement_id = e.id "
            "WHERE e.owner_id = :o"
        ),
        {"o": alice.user_id},
    ).one()
    assert (row.source, row.code_id, row.r_code, row.span.days) == ("code", code_id, code_id, 30)
    plan = (await alice.get("/api/account/plan")).json()
    assert (plan["plan"], plan["source"], plan["features"]) == (
        "instructor",
        "code",
        ["song_picking"],
    )


async def test_parallel_redeems_of_the_last_use_give_exactly_one_entitlement(
    admin: Connection, browsers: Any
) -> None:
    users: list[Browser] = [await browsers(mowl=False) for _ in range(8)]
    code, code_id = plant_code(admin, uses=3, used=2)
    headers = [await b.headers() for b in users]
    responses = await asyncio.gather(
        *(
            b.client.post(REDEEM, json={"code": code}, headers=h)
            for b, h in zip(users, headers, strict=True)
        )
    )
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200] + [400] * 7, [r.text for r in responses]
    assert {r.json()["detail"]["reason"] for r in responses if r.status_code == 400} == {
        "code_invalid"
    }
    assert redeemed_count(admin, code_id) == 3
    assert (
        admin.execute(
            text("SELECT count(*) FROM entitlements WHERE code_id = :c"), {"c": code_id}
        ).scalar_one()
        == 1
    )
    assert (
        admin.execute(
            text("SELECT count(*) FROM code_redemptions WHERE code_id = :c"), {"c": code_id}
        ).scalar_one()
        == 1
    )


async def test_the_users_second_redeem_is_already_redeemed_and_counts_nothing(
    admin: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    code, code_id = plant_code(admin, uses=5)
    assert (await alice.post(REDEEM, {"code": code})).status_code == 200
    for _ in range(2):
        again = await alice.post(REDEEM, {"code": code})
        assert (again.status_code, again.json()) == (
            409,
            {"detail": {"reason": "already_redeemed"}},
        )
    assert redeemed_count(admin, code_id) == 1
    assert (
        admin.execute(
            text("SELECT count(*) FROM entitlements WHERE owner_id = :o"), {"o": alice.user_id}
        ).scalar_one()
        == 1
    )
    # Parallel repeats by the same user change nothing either (the row lock serialises them).
    headers = await alice.headers()
    repeats = await asyncio.gather(
        *(alice.client.post(REDEEM, json={"code": code}, headers=headers) for _ in range(4))
    )
    assert {r.status_code for r in repeats} == {409}
    assert redeemed_count(admin, code_id) == 1


async def test_parallel_first_redeems_by_one_user_give_one_entitlement(
    admin: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    code, code_id = plant_code(admin, uses=5)
    headers = await alice.headers()
    responses = await asyncio.gather(
        *(alice.client.post(REDEEM, json={"code": code}, headers=headers) for _ in range(5))
    )
    assert sorted(r.status_code for r in responses) == [200, 409, 409, 409, 409]
    assert redeemed_count(admin, code_id) == 1


async def test_unknown_expired_disabled_exhausted_and_malformed_codes_look_the_same(
    admin: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    unknown, _ = codes.new_code()  # never stored
    expired, expired_id = plant_code(admin, expires="now() - interval '1 second'")
    disabled, disabled_id = plant_code(admin, disabled=True)
    exhausted, exhausted_id = plant_code(admin, uses=2, used=2)
    answers = []
    for code in (unknown, expired, disabled, exhausted, "not-a-code", unknown[:-1] + "U"):
        response = await alice.post(REDEEM, {"code": code})
        answers.append((response.status_code, response.content))
    assert answers == [(400, b'{"detail":{"reason":"code_invalid"}}')] * 6
    assert [redeemed_count(admin, c) for c in (expired_id, disabled_id, exhausted_id)] == [0, 0, 2]
    assert admin.execute(text("SELECT count(*) FROM entitlements")).scalar_one() == 0


async def test_redeem_is_rate_limited_per_user(admin: Connection, browsers: Any) -> None:
    alice: Browser = await browsers(mowl=False)
    unknown, _ = codes.new_code()
    for _ in range(10):
        assert (await alice.post(REDEEM, {"code": unknown})).status_code == 400
    code, code_id = plant_code(admin)  # a good code is refused too once the limit is hit
    limited = await alice.post(REDEEM, {"code": code})
    assert limited.status_code == 429
    assert limited.json() == {"detail": {"reason": "rate_limited"}}
    assert int(limited.headers["Retry-After"]) > 0
    assert redeemed_count(admin, code_id) == 0
    bob: Browser = await browsers(mowl=False)  # another user (same IP, below its 30) still can
    assert (await bob.post(REDEEM, {"code": code})).status_code == 200


async def test_redeem_is_rate_limited_per_ip(admin: Connection, browsers: Any) -> None:
    unknown, _ = codes.new_code()
    users: list[Browser] = [await browsers(mowl=False) for _ in range(4)]
    for user in users[:3]:
        for _ in range(10):
            assert (await user.post(REDEEM, {"code": unknown})).status_code == 400
    assert (await users[3].post(REDEEM, {"code": unknown})).status_code == 429


async def test_redeem_needs_csrf_and_a_session(admin: Connection, browsers: Any, env: Any) -> None:
    alice: Browser = await browsers(mowl=False)
    code, code_id = plant_code(admin)
    headers = await alice.headers()
    del headers["X-CSRF-Token"]
    refused = await alice.client.post(REDEEM, json={"code": code}, headers=headers)
    alice.client.cookies.clear()
    assert refused.status_code == 403
    assert refused.json()["detail"]["reason"].startswith("csrf_")
    anonymous = await env.client.post(
        REDEEM,
        json={"code": code},
        headers={"Origin": "https://classbeat.test", "X-CSRF-Token": "x"},
    )
    assert anonymous.status_code == 403  # no session: the double-submit cookie is missing
    assert redeemed_count(admin, code_id) == 0


async def test_overlapping_codes_dont_add_up_and_the_highest_plan_wins(
    admin: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    first, _ = plant_code(admin, plan="instructor", days=30)
    second, _ = plant_code(admin, plan="instructor", days=30)
    assert (await alice.post(REDEEM, {"code": first})).status_code == 200
    assert (await alice.post(REDEEM, {"code": second})).status_code == 200
    plan = (await alice.get("/api/account/plan")).json()
    assert plan["plan"] == "instructor"
    assert about(plan["ends_at"], 30)  # not 60: the second code gives no extra days
    pro, _ = plant_code(admin, plan="pro", days=7)
    assert (await alice.post(REDEEM, {"code": pro})).status_code == 200
    plan = (await alice.get("/api/account/plan")).json()
    assert (plan["plan"], plan["limits"]["classes_per_month"]) == ("pro", 100)
    assert about(plan["ends_at"], 7)
    # Once the Pro code has ended the Instructor ones apply again (never the sum of anything).
    admin.execute(
        text(
            "UPDATE entitlements SET starts_at = now() - interval '8 days', "
            "ends_at = now() - interval '1 second' WHERE owner_id = :o AND plan_id = 'pro'"
        ),
        {"o": alice.user_id},
    )
    assert (await alice.get("/api/account/plan")).json()["plan"] == "instructor"


async def test_every_code_entitlement_ends_by_the_codes_terms(
    admin: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    for days in (1, 366):
        code, _ = plant_code(admin, plan="pro", days=days)
        assert (await alice.post(REDEEM, {"code": code})).status_code == 200
    spans = admin.execute(
        text(
            "SELECT e.ends_at - e.starts_at AS span, c.grant_days FROM entitlements e "
            "JOIN redeem_codes c ON c.id = e.code_id WHERE e.owner_id = :o ORDER BY c.grant_days"
        ),
        {"o": alice.user_id},
    ).all()
    assert [(r.span.days, r.grant_days) for r in spans] == [(1, 1), (366, 366)]


async def test_codes_never_reach_a_log_or_an_error_body(
    admin: Connection, browsers: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    alice: Browser = await browsers(mowl=False)
    good, _ = plant_code(admin)
    expired, _ = plant_code(admin, expires="now() - interval '1 second'")
    unknown, _ = codes.new_code()
    too_long = unknown * 3
    bodies = []
    for code in (good, good, expired, unknown, unknown.lower(), too_long):
        bodies.append((await alice.post(REDEEM, {"code": code})).text)
    assert (await alice.post(REDEEM, {"code": too_long})).status_code == 422
    out = io.StringIO()
    formatter = JsonFormatter()
    for record in caplog.records:
        out.write(formatter.format(record) + "\n")
    logged = out.getvalue() + caplog.text
    assert caplog.records  # the access log at least
    for code in (good, expired, unknown):
        for form in (code, code.lower(), code[:13], code[13:]):
            assert form not in logged
            assert all(form not in body for body in bodies)
