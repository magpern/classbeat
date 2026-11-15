"""The player link routes' guards (ADR 0034 section 3, ADR 0003 sections 4 and 6): start and
disconnect need recent authentication, with the step-up once TOTP is confirmed, and pass the CSRF
and Origin checks; the callback acts only when it arrives at the fixed callback address. A
refused request never reaches the player and changes no flow or row."""

from typing import Any

from authtools import ORIGIN, session_cookie
from mfatools import Actor
from playertools import CALLBACK, START, STATUS, Linker


async def snapshot(linker: Linker, actor: Actor) -> tuple[Any, ...]:
    user_id = linker.user_id(actor)
    row = await linker.connection(user_id)
    flows = [tuple(f) for f in await linker.flows(user_id)]
    return (None if row is None else tuple(row), flows, len(linker.fake.seen))


async def refused(actor: Actor, reason: str) -> None:
    for method, path in (("POST", START), ("DELETE", STATUS)):
        answer = await actor.call(method, path)
        assert (answer.status_code, answer.json()["detail"]["reason"]) == (403, reason), path


async def test_start_and_disconnect_need_recent_auth(linker: Linker) -> None:
    alice = await linker.actor("alice@example.com")
    await linker.connect(alice)
    await linker.env.run("UPDATE sessions SET reauth_at = now() - interval '11 minutes'")
    before = await snapshot(linker, alice)
    await refused(alice, "reauth_required")
    assert await snapshot(linker, alice) == before


async def test_with_totp_they_also_need_the_step_up(linker: Linker) -> None:
    alice = await linker.actor("alice@example.com")
    await linker.connect(alice)
    await alice.enroll()
    await linker.env.run("UPDATE sessions SET reauth_at = now(), stepup_at = NULL")
    before = await snapshot(linker, alice)
    await refused(alice, "stepup_required")
    assert await snapshot(linker, alice) == before


async def test_a_signed_in_request_without_csrf_or_from_another_origin_is_refused(
    linker: Linker,
) -> None:
    alice = await linker.actor("alice@example.com")
    await linker.connect(alice)
    before = await snapshot(linker, alice)
    info = await alice.client.get("/api/auth/session", headers=session_cookie(alice.token))
    csrf = info.json()["csrf_token"]
    alice.client.cookies.clear()
    for method, path in (("POST", START), ("DELETE", STATUS)):
        for headers in (
            {"Origin": ORIGIN},  # no CSRF token
            {"Origin": "https://evil.example", "X-CSRF-Token": csrf},  # another origin
        ):
            answer = await alice.client.request(
                method, path, headers={**headers, **session_cookie(alice.token)}
            )
            alice.client.cookies.clear()
            assert answer.status_code == 403, (method, headers)
    assert await snapshot(linker, alice) == before


async def test_a_callback_at_another_host_ends_the_own_flow_without_a_call(
    linker: Linker,
) -> None:
    alice = await linker.actor("alice@example.com")
    query = linker.fake.approve(await linker.start(alice))
    response = await alice.client.get(
        CALLBACK,
        params=query,
        headers={**session_cookie(alice.token), "Host": "other.classbeat.test"},
    )
    alice.client.cookies.clear()
    assert response.status_code == 302
    assert response.headers["location"] == "/account/player?result=access_denied"
    assert [r for r in linker.fake.seen if r.url.path == "/api/source/token"] == []
    assert await linker.flows(linker.user_id(alice)) == []
    assert await linker.connection(linker.user_id(alice)) is None
