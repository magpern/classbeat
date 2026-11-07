"""Turnstile verification without a database (ADR 0025 sections 3, 4, 8, 9 and 10): the answer
checks, the real client against an ``httpx.MockTransport`` (no network; netguard stays on), its
retries, deadline and concurrency cap, the e2e fake, the egress client and the CSP sources."""

import asyncio
import gzip
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from classbeat import egress
from classbeat.auth import captcha
from classbeat.auth.captcha import (
    CaptchaFailed,
    CaptchaUnavailable,
    FakeTurnstileVerifier,
    TurnstileVerifier,
    check_answer,
    token_problem,
)
from classbeat.headers import TURNSTILE_SOURCE, content_security_policy, csp_directives

HOST = "classbeat.test"
SECRET = "0x-test-secret-for-the-mock-transport-only"  # never a real key
NOW = 1_800_000_000.0


def stamp(offset: float = 0.0) -> str:
    return datetime.fromtimestamp(NOW + offset, UTC).isoformat().replace("+00:00", "Z")


def passed(action: str | None = "signin", **changes: object) -> dict[str, object]:
    answer: dict[str, object] = {
        "success": True,
        "hostname": HOST,
        "action": action,
        "challenge_ts": stamp(-5),
        "error-codes": [],
    }
    answer.update(changes)
    return answer


def refused(*codes: str) -> dict[str, object]:
    return {"success": False, "error-codes": list(codes)}


# --- check_answer and token_problem -----------------------------------------------------------


def judge(answer: object, action: str = "signin") -> None:
    check_answer(answer, hostname=HOST, action=action, now=NOW)


def test_a_good_answer_passes() -> None:
    judge(passed())
    judge(passed("reset"), "reset")


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        (passed(hostname="evil.test"), "hostname"),
        (passed(hostname="example.com"), "hostname"),  # what Cloudflare's test secrets answer
        (passed(hostname=None), "hostname"),
        (passed("reset"), "action"),  # a token solved on the reset page cannot sign in
        (passed(action=None), "action"),  # test secrets answer no action
        (passed(challenge_ts=stamp(-301)), "stale"),
        (passed(challenge_ts=stamp(61)), "stale"),
        (passed(challenge_ts="yesterday"), "stale"),
        (passed(challenge_ts="2026-11-07T10:00:00"), "stale"),  # no zone
        (passed(challenge_ts=None), "stale"),
        (passed(success="true"), "unknown"),  # success must be literally true
        (passed(success=1), "unknown"),
        (refused("missing-input-response"), "missing-input-response"),
        (refused("invalid-input-response"), "invalid-input-response"),
        (refused("timeout-or-duplicate"), "timeout-or-duplicate"),
        (refused(), "unknown"),
        (refused("something-new"), "unknown"),
        ({"success": False, "error-codes": "timeout-or-duplicate"}, "unknown"),
    ],
)
def test_every_failed_check_is_captcha_failed(answer: object, why: str) -> None:
    with pytest.raises(CaptchaFailed) as caught:
        judge(answer)
    assert caught.value.why == why


@pytest.mark.parametrize(
    ("answer", "why", "misconfigured"),
    [
        (refused("internal-error"), "internal-error", False),
        (refused("missing-input-secret"), "missing-input-secret", True),
        (refused("invalid-input-secret"), "invalid-input-secret", True),
        (refused("bad-request"), "bad-request", True),
        (refused("timeout-or-duplicate", "invalid-input-secret"), "invalid-input-secret", True),
        ([1, 2], "bad_json", False),
        ("ok", "bad_json", False),
    ],
)
def test_our_side_and_cloudflare_trouble_is_unavailable(
    answer: object, why: str, misconfigured: bool
) -> None:
    with pytest.raises(CaptchaUnavailable) as caught:
        judge(answer)
    assert (caught.value.why, caught.value.misconfigured) == (why, misconfigured)


def test_the_challenge_time_window_edges_pass() -> None:
    judge(passed(challenge_ts=stamp(-300)))
    judge(passed(challenge_ts=stamp(60)))


@pytest.mark.parametrize(
    ("token", "why"),
    [
        (None, "missing"),
        ("", "missing"),
        ("x" * 2049, "malformed"),
        ("tök", "malformed"),
        ("a\nb", "malformed"),
        ("a\x00b", "malformed"),
        (12345, "malformed"),
        (["a"], "malformed"),
        ({"t": "a"}, "malformed"),
    ],
)
def test_impossible_tokens_are_refused_without_a_call(token: object, why: str) -> None:
    assert token_problem(token) == why


def test_a_plausible_token_is_left_to_the_verifier() -> None:
    assert token_problem("x" * 2048) is None
    assert token_problem("0.AbC-_xyz") is None


# --- the real client against a mock transport ---------------------------------------------------


class Siteverify:
    """A scripted siteverify endpoint: each call takes the next step (a dict answer, a status, or
    an exception to raise) and keeps the request it got."""

    def __init__(self, *steps: object) -> None:
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return await step(request)  # pyright: ignore[reportUnknownVariableType, reportGeneralTypeIssues]
        if isinstance(step, int):
            return httpx.Response(step, json=refused("internal-error"))
        return httpx.Response(200, json=step)

    def bodies(self) -> list[dict[str, str]]:
        return [json.loads(r.content) for r in self.requests]


def verifier(endpoint: Callable[..., object], **kwargs: object) -> TurnstileVerifier:
    transport = httpx.MockTransport(endpoint)  # pyright: ignore[reportArgumentType]
    return TurnstileVerifier(
        SecretStr(SECRET),
        HOST,
        client=lambda: egress.turnstile_test_client(transport),
        clock=lambda: NOW,
        **kwargs,  # pyright: ignore[reportArgumentType]
    )


async def test_a_pass_sends_secret_token_address_and_key_and_nothing_else() -> None:
    endpoint = Siteverify(passed())
    await verifier(endpoint).verify("tok-1", "signin", "203.0.113.9")
    (request,) = endpoint.requests
    assert str(request.url) == captcha.SITEVERIFY_URL
    assert request.method == "POST"
    body = endpoint.bodies()[0]
    assert set(body) == {"secret", "response", "remoteip", "idempotency_key"}
    assert (body["secret"], body["response"], body["remoteip"]) == (SECRET, "tok-1", "203.0.113.9")
    assert len(body["idempotency_key"]) == 36
    # No default header: no cookie, no user agent, no authorization; only what the call sets.
    assert "user-agent" not in request.headers
    assert "cookie" not in request.headers
    assert request.headers["accept-encoding"] == "identity"


async def test_no_usable_address_means_no_remoteip() -> None:
    endpoint = Siteverify(passed())
    await verifier(endpoint).verify("tok-1", "signin", None)
    assert "remoteip" not in endpoint.bodies()[0]


async def test_each_verification_has_its_own_idempotency_key() -> None:
    endpoint = Siteverify(passed())
    check = verifier(endpoint)
    await check.verify("tok-1", "signin", None)
    await check.verify("tok-2", "signin", None)
    first, second = endpoint.bodies()
    assert first["idempotency_key"] != second["idempotency_key"]


async def test_the_action_and_host_are_checked_on_the_answer() -> None:
    with pytest.raises(CaptchaFailed) as caught:
        await verifier(Siteverify(passed("signup"))).verify("tok", "reset", None)
    assert caught.value.why == "action"
    with pytest.raises(CaptchaFailed) as caught:
        await verifier(Siteverify(passed(hostname="other.test"))).verify("tok", "signin", None)
    assert caught.value.why == "hostname"


async def test_a_reused_token_fails() -> None:
    with pytest.raises(CaptchaFailed) as caught:
        await verifier(Siteverify(refused("timeout-or-duplicate"))).verify("t", "signin", None)
    assert caught.value.why == "timeout-or-duplicate"


@pytest.mark.parametrize(
    "first",
    [
        503,
        500,
        refused("internal-error"),
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("slow"),
        httpx.ReadTimeout("lost answer"),
    ],
)
async def test_one_retry_with_the_same_idempotency_key(first: object) -> None:
    endpoint = Siteverify(first, passed())
    await verifier(endpoint).verify("tok", "signin", "203.0.113.9")
    assert len(endpoint.requests) == 2
    one, two = endpoint.bodies()
    assert one == two  # the same key, token and address: a lost success stays a success


@pytest.mark.parametrize(
    ("steps", "why"),
    [
        ((502, 503), "http_503"),
        ((refused("internal-error"), refused("internal-error")), "internal-error"),
        ((httpx.ConnectError("x"), httpx.ConnectError("x")), "network"),
        ((httpx.ReadTimeout("x"), httpx.ReadTimeout("x")), "timeout"),
    ],
)
async def test_two_failures_are_unavailable(steps: tuple[object, ...], why: str) -> None:
    endpoint = Siteverify(*steps)
    with pytest.raises(CaptchaUnavailable) as caught:
        await verifier(endpoint).verify("tok", "signin", None)
    assert caught.value.why == why
    assert len(endpoint.requests) == 2  # never a third


@pytest.mark.parametrize(
    ("step", "why"),
    [
        (400, "http_400"),
        (403, "http_403"),
        (429, "http_429"),
        (httpx.RemoteProtocolError("x"), "network"),
        (refused("invalid-input-secret"), "invalid-input-secret"),
        (refused("bad-request"), "bad-request"),
    ],
)
async def test_what_is_not_retried(step: object, why: str) -> None:
    endpoint = Siteverify(step, passed())
    with pytest.raises(CaptchaUnavailable) as caught:
        await verifier(endpoint).verify("tok", "signin", None)
    assert caught.value.why == why
    assert len(endpoint.requests) == 1


async def test_a_failed_token_is_not_retried() -> None:
    endpoint = Siteverify(refused("invalid-input-response"), passed())
    with pytest.raises(CaptchaFailed):
        await verifier(endpoint).verify("tok", "signin", None)
    assert len(endpoint.requests) == 1


def raw(status: int, content: bytes, headers: dict[str, str] | None = None):
    async def answer(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content, headers=headers or {})

    return answer


@pytest.mark.parametrize(
    ("content", "headers"),
    [
        (b"not json", None),
        (b"", None),
        (b'{"success": true, "pad": "' + b"x" * 9000 + b'"}', None),  # over 8 KiB
        (gzip.compress(json.dumps(passed()).encode()), {"content-encoding": "gzip"}),
    ],
)
async def test_an_unreadable_answer_is_unavailable(
    content: bytes, headers: dict[str, str] | None
) -> None:
    endpoint = Siteverify(raw(200, content, headers))
    with pytest.raises(CaptchaUnavailable) as caught:
        await verifier(endpoint).verify("tok", "signin", None)
    assert caught.value.why == "bad_json"


async def test_a_redirect_is_never_followed() -> None:
    async def redirect(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://challenges.cloudflare.com/x"})

    endpoint = Siteverify(redirect)
    with pytest.raises(CaptchaUnavailable) as caught:
        await verifier(endpoint).verify("tok", "signin", None)
    assert caught.value.why == "http_302"
    assert len(endpoint.requests) == 1


async def test_the_whole_verification_has_one_deadline() -> None:
    async def hang(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        raise AssertionError("not reached")

    endpoint = Siteverify(hang)
    started = time.monotonic()
    with pytest.raises(CaptchaUnavailable) as caught:
        await verifier(endpoint, deadline=0.2).verify("tok", "signin", None)
    assert caught.value.why == "timeout"
    assert time.monotonic() - started < 2


def test_the_production_deadlines_are_the_adrs() -> None:
    assert captcha.VERIFY_SECONDS == 5.0
    assert egress.TURNSTILE_TOTAL_SECONDS == 5.0
    assert egress.TURNSTILE_CONNECT_SECONDS == 3.0
    assert captcha.MAX_IN_FLIGHT == 16
    assert captcha.SLOT_WAIT_SECONDS == 1.0
    assert captcha.BODY_MAX == 8192
    assert captcha.TOKEN_MAX == 2048


async def test_at_most_n_calls_in_flight_and_the_next_waits_then_gives_up() -> None:
    release = asyncio.Event()
    all_started = asyncio.Event()
    started = 0

    async def slow(_: httpx.Request) -> httpx.Response:
        nonlocal started
        started += 1
        if started == 3:
            all_started.set()
        await release.wait()
        return httpx.Response(200, json=passed())

    endpoint = Siteverify(slow)
    check = verifier(endpoint, max_in_flight=3, slot_wait=0.1)
    holders = [asyncio.create_task(check.verify(f"t{i}", "signin", None)) for i in range(3)]
    await asyncio.wait_for(all_started.wait(), 5)
    with pytest.raises(CaptchaUnavailable) as caught:
        await check.verify("t-extra", "signin", None)
    assert caught.value.why == "busy"
    assert started == 3  # the refused one made no call
    release.set()
    await asyncio.gather(*holders)
    await check.verify("t-after", "signin", None)  # slots are given back
    assert started == 4


async def test_the_secret_is_never_in_a_repr_or_an_exception() -> None:
    check = verifier(Siteverify(httpx.ConnectError(f"boom {SECRET}"), httpx.ConnectError("x")))
    assert SECRET not in repr(check)
    with pytest.raises(CaptchaUnavailable) as caught:
        await check.verify("tok", "signin", None)
    assert SECRET not in str(caught.value)
    # Raised ``from None``: no chained exception carries the request or its message to a log.
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


# --- the egress client ----------------------------------------------------------------------


def test_the_production_client_refuses_inside_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(egress, "_worker_process", True)
    with pytest.raises(egress.OutboundBlocked):
        egress.turnstile_async_client()


async def test_the_production_client_has_one_host_and_no_extras() -> None:
    client = egress.turnstile_async_client()
    try:
        assert client.follow_redirects is False
        assert dict(client.headers) == {}
        assert client.timeout.connect == 3.0
        with pytest.raises(egress.OutboundBlocked):
            await client.post("https://api.anthropic.com/v1/x", json={})
        with pytest.raises(egress.OutboundBlocked):
            await client.post("http://challenges.cloudflare.com/turnstile/v0/siteverify")
        with pytest.raises(egress.OutboundBlocked):
            await client.post("https://challenges.cloudflare.com:8443/turnstile/v0/siteverify")
    finally:
        await client.aclose()


def test_the_host_is_on_no_urllib_list() -> None:
    assert egress.TURNSTILE_HOST == "challenges.cloudflare.com"
    assert egress.TURNSTILE_HOST not in egress.WEB_URLLIB_HOSTS
    assert egress.TURNSTILE_HOST not in egress.worker_hosts(["anthropic", "openai", "deepseek"])


# --- the e2e fake ----------------------------------------------------------------------------


async def test_the_fake_passes_a_pass_token_once() -> None:
    fake = FakeTurnstileVerifier(HOST, clock=lambda: NOW)
    await fake.verify("fake-pass-abc", "signup", None)
    with pytest.raises(CaptchaFailed) as caught:
        await fake.verify("fake-pass-abc", "signup", None)
    assert caught.value.why == "timeout-or-duplicate"
    await fake.verify("fake-pass-def", "reset", None)


@pytest.mark.parametrize(
    "token", ["fake-fail", "fake-pass-", "anything", "1x00000000000000000000AA"]
)
async def test_the_fake_fails_everything_else(token: str) -> None:
    with pytest.raises(CaptchaFailed):
        await FakeTurnstileVerifier(HOST).verify(token, "signin", None)


async def test_the_fake_can_be_unavailable() -> None:
    with pytest.raises(CaptchaUnavailable) as caught:
        await FakeTurnstileVerifier(HOST).verify("fake-unavailable", "signin", None)
    assert caught.value.why == "internal-error"


async def test_the_fakes_memory_is_bounded() -> None:
    fake = FakeTurnstileVerifier(HOST)
    for i in range(captcha._FAKE_REMEMBERED + 5):  # pyright: ignore[reportPrivateUsage]
        await fake.verify(f"fake-pass-{i}", "signin", None)
    assert len(fake._spent) == captcha._FAKE_REMEMBERED  # pyright: ignore[reportPrivateUsage]


# --- CSP ---------------------------------------------------------------------------------------


def test_the_csp_names_cloudflare_only_with_real_turnstile() -> None:
    off = content_security_policy("https://classbeat.test")
    assert TURNSTILE_SOURCE not in off
    on = dict(d.split(" ", 1) for d in csp_directives("https://classbeat.test", turnstile=True))
    assert on["script-src"] == "'self' https://challenges.cloudflare.com"
    assert on["frame-src"] == (
        "https://challenges.cloudflare.com https://classbeat.test/api/admin/mail/"
    )
    assert on["connect-src"] == "'self'"  # no pre-clearance
    # Everything else is the same as with Turnstile off.
    off_map = dict(d.split(" ", 1) for d in csp_directives("https://classbeat.test"))
    assert {k: v for k, v in on.items() if k not in ("script-src", "frame-src")} == {
        k: v for k, v in off_map.items() if k not in ("script-src", "frame-src")
    }
    assert "unsafe-inline" not in content_security_policy(None, turnstile=True)
    assert dict(d.split(" ", 1) for d in csp_directives(None, turnstile=True))["frame-src"] == (
        TURNSTILE_SOURCE
    )


def test_a_stale_answer_by_a_day() -> None:
    old = (datetime.fromtimestamp(NOW, UTC) - timedelta(days=1)).isoformat()
    with pytest.raises(CaptchaFailed):
        judge(passed(challenge_ts=old))
