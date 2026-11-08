"""Cloudflare Turnstile on the anonymous auth forms (ADR 0025).

Four routes call ``require_captcha`` as their first step after the body's shape (section 2):
sign-in (action ``signin``), sign-up (``signup``), the provider-email step (``signup_email``) and
the reset request (``reset``). It reserves the per-IP captcha bucket, refuses a missing, overlong or
non-ASCII token without a network call, and asks the verifier on ``app.state.captcha``. Nothing
about an account is read, counted, hashed, mailed or started before it passes, so its answer does
not depend on whether an account exists.

Answers (section 3): any failed check is 400 ``captcha_failed``; Cloudflare unreachable, slow, a
non-2xx, an unreadable answer, ``internal-error``, a misconfiguration on our side, or all 16
verification slots taken, is 503 ``captcha_unavailable`` with ``Retry-After: 30`` (fail closed,
section 5). One log line per refusal names the action and a fixed ``why`` word, never the token,
the secret, the idempotency key, the address or the body.

``TurnstileVerifier`` is the real one (``classbeat.egress.turnstile_async_client``);
``FakeTurnstileVerifier`` is the e2e stack's (``CLASSBEAT_TURNSTILE_FAKE``, refused in prod by
``Settings``). Both judge Cloudflare's answer with ``check_answer``.
"""

import asyncio
import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final, Literal, Protocol

import httpx
from fastapi import HTTPException, Request
from pydantic import SecretStr

from classbeat.auth.clientip import bucket_ip, parse_address
from classbeat.auth.deps import request_ip
from classbeat.auth.flowtools import reserve
from classbeat.auth.ratelimit import CAPTCHA_IP, Bucket
from classbeat.config import Settings
from classbeat.egress import OutboundBlocked, turnstile_async_client

logger = logging.getLogger(__name__)

CaptchaAction = Literal["signin", "signup", "signup_email", "reset"]

SITEVERIFY_URL: Final = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TOKEN_MAX: Final = 2048  # Cloudflare's own limit on a token
BODY_MAX: Final = 8 * 1024  # a siteverify answer is a few hundred bytes
VERIFY_SECONDS: Final = 5.0  # both attempts together (section 4)
MAX_IN_FLIGHT: Final = 16  # siteverify calls per web process
SLOT_WAIT_SECONDS: Final = 1.0
MAX_AGE_SECONDS: Final = 300  # a token lives 300 s
MAX_AHEAD_SECONDS: Final = 60  # clock skew we accept for a challenge_ts in the future
RETRY_AFTER: Final = "30"

# Cloudflare's error codes, by what they mean for the request.
FAILURE_CODES: Final = ("missing-input-response", "invalid-input-response", "timeout-or-duplicate")
MISCONFIGURED_CODES: Final = ("missing-input-secret", "invalid-input-secret", "bad-request")
INTERNAL_ERROR: Final = "internal-error"

FAILED: Final = {"reason": "captcha_failed"}
UNAVAILABLE: Final = {"reason": "captcha_unavailable"}


class CaptchaRefused(Exception):
    """A refusal; ``why`` is one fixed word for the log (never anything from the request)."""

    def __init__(self, why: str, *, misconfigured: bool = False) -> None:
        super().__init__(why)
        self.why = why
        self.misconfigured = misconfigured


class CaptchaFailed(CaptchaRefused):
    """The token did not pass: 400 ``captcha_failed``."""


class CaptchaUnavailable(CaptchaRefused):
    """We could not get a verdict: 503 ``captcha_unavailable``."""


class _Retryable(Exception):
    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


class CaptchaVerifier(Protocol):
    async def verify(self, token: str, action: CaptchaAction, remoteip: str | None) -> None:
        """Return when the token passes; raise ``CaptchaFailed`` or ``CaptchaUnavailable``."""
        ...


def token_problem(token: object) -> str | None:
    """``missing`` or ``malformed`` for a token that cannot be one, else ``None`` (no network)."""
    if token is None or token == "":
        return "missing"
    if (
        not isinstance(token, str)
        or len(token) > TOKEN_MAX
        or not token.isascii()
        or not token.isprintable()
    ):
        return "malformed"
    return None


def _challenge_time(value: object) -> float | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return None if parsed.tzinfo is None else parsed.timestamp()


def check_answer(answer: object, *, hostname: str, action: str, now: float) -> None:
    """Judge a siteverify answer (section 4). ``success`` must be literally ``true``, with our
    host, the route's action and a ``challenge_ts`` at most 300 s old (60 s ahead at most)."""
    if not isinstance(answer, dict):
        raise CaptchaUnavailable("bad_json")
    fields: dict[str, Any] = answer  # pyright: ignore[reportUnknownVariableType]
    if fields.get("success") is True:
        if fields.get("hostname") != hostname:
            raise CaptchaFailed("hostname")
        if fields.get("action") != action:
            raise CaptchaFailed("action")
        solved = _challenge_time(fields.get("challenge_ts"))
        if solved is None or not now - MAX_AGE_SECONDS <= solved <= now + MAX_AHEAD_SECONDS:
            raise CaptchaFailed("stale")
        return
    raw = fields.get("error-codes")
    codes = [c for c in raw if isinstance(c, str)] if isinstance(raw, list) else []  # pyright: ignore[reportUnknownVariableType]
    for code in MISCONFIGURED_CODES:
        if code in codes:
            raise CaptchaUnavailable(code, misconfigured=True)
    if INTERNAL_ERROR in codes:
        raise CaptchaUnavailable(INTERNAL_ERROR)
    for code in FAILURE_CODES:
        if code in codes:
            raise CaptchaFailed(code)
    raise CaptchaFailed("unknown")


def _internal_error(answer: object) -> bool:
    if not isinstance(answer, dict) or answer.get("success") is True:  # pyright: ignore[reportUnknownMemberType]
        return False
    codes = answer.get("error-codes")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return isinstance(codes, list) and INTERNAL_ERROR in codes


class TurnstileVerifier:
    """Cloudflare's siteverify (section 4): at most ``MAX_IN_FLIGHT`` calls at once (a request
    that waits ``SLOT_WAIT_SECONDS`` for a slot is unavailable), one deadline of
    ``VERIFY_SECONDS`` per verification, and a second attempt only after a connect error, a read
    timeout, a 5xx or ``internal-error``, with the same idempotency key, so a retry after a lost
    answer cannot turn a success into ``timeout-or-duplicate``."""

    def __init__(
        self,
        secret: SecretStr,
        hostname: str,
        *,
        client: Callable[[], httpx.AsyncClient] = turnstile_async_client,
        clock: Callable[[], float] = time.time,
        max_in_flight: int = MAX_IN_FLIGHT,
        slot_wait: float = SLOT_WAIT_SECONDS,
        deadline: float = VERIFY_SECONDS,
    ) -> None:
        self._secret = secret
        self._hostname = hostname
        self._client = client
        self._clock = clock
        self._slots = asyncio.Semaphore(max_in_flight)
        self._slot_wait = slot_wait
        self._deadline = deadline

    def __repr__(self) -> str:
        return "TurnstileVerifier()"

    async def verify(self, token: str, action: CaptchaAction, remoteip: str | None) -> None:
        try:
            async with asyncio.timeout(self._slot_wait):
                await self._slots.acquire()
        except TimeoutError:
            raise CaptchaUnavailable("busy") from None
        try:
            answer = await self._siteverify(token, remoteip)
        finally:
            self._slots.release()
        check_answer(answer, hostname=self._hostname, action=action, now=self._clock())

    async def _siteverify(self, token: str, remoteip: str | None) -> object:
        body: dict[str, str] = {
            "secret": self._secret.get_secret_value(),
            "response": token,
            "idempotency_key": str(uuid.uuid4()),  # one per verification, both attempts
        }
        if remoteip is not None:
            body["remoteip"] = remoteip
        try:
            async with asyncio.timeout(self._deadline):
                async with self._client() as client:
                    try:
                        answer = await _post(client, body)
                    except _Retryable:
                        return await _post_last(client, body)
                    if _internal_error(answer):
                        return await _post_last(client, body)
                    return answer
        except TimeoutError:  # the deadline, or the transport's own total deadline
            raise CaptchaUnavailable("timeout") from None


async def _post_last(client: httpx.AsyncClient, body: dict[str, str]) -> object:
    try:
        return await _post(client, body)
    except _Retryable as exc:
        raise CaptchaUnavailable(exc.why) from None


async def _post(client: httpx.AsyncClient, body: dict[str, str]) -> object:
    """One siteverify request: the parsed answer, ``_Retryable`` for what may be retried, else
    ``CaptchaUnavailable``. No exception text is kept: it could hold the request."""
    try:
        async with client.stream(
            "POST",
            SITEVERIFY_URL,
            json=body,
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        ) as response:
            status = response.status_code
            if status >= 500:
                raise _Retryable(f"http_{status}")
            if not 200 <= status < 300:
                raise CaptchaUnavailable(f"http_{status}")
            if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                raise CaptchaUnavailable("bad_json")
            data = bytearray()
            async for chunk in response.aiter_raw():
                data += chunk
                if len(data) > BODY_MAX:
                    raise CaptchaUnavailable("bad_json")
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise _Retryable("network") from None
    except httpx.ReadTimeout:
        # The request may have arrived and only its answer been lost: retried with the same key.
        raise _Retryable("timeout") from None
    except (httpx.HTTPError, OutboundBlocked):
        raise CaptchaUnavailable("network") from None
    try:
        return json.loads(bytes(data))
    except ValueError:
        raise CaptchaUnavailable("bad_json") from None


FAKE_PASS_PREFIX: Final = "fake-pass-"  # noqa: S105 - the fake widget's token, not a secret
FAKE_UNAVAILABLE: Final = "fake-unavailable"
_FAKE_REMEMBERED: Final = 10_000


class FakeTurnstileVerifier:
    """The e2e stack's verifier (``CLASSBEAT_TURNSTILE_FAKE``, section 8; ``Settings`` refuses it
    in prod). No network. ``fake-pass-<anything>`` passes once (a second use is
    ``timeout-or-duplicate``, as Cloudflare's single use), ``fake-unavailable`` is Cloudflare's
    ``internal-error``, anything else ``invalid-input-response``. The answer goes through the same
    ``check_answer`` as Cloudflare's."""

    def __init__(self, hostname: str, *, clock: Callable[[], float] = time.time) -> None:
        self._hostname = hostname
        self._clock = clock
        self._spent: OrderedDict[str, None] = OrderedDict()

    async def verify(self, token: str, action: CaptchaAction, remoteip: str | None) -> None:
        now = self._clock()
        answer: dict[str, object]
        if token == FAKE_UNAVAILABLE:
            answer = {"success": False, "error-codes": [INTERNAL_ERROR]}
        elif token in self._spent:
            answer = {"success": False, "error-codes": ["timeout-or-duplicate"]}
        elif token.startswith(FAKE_PASS_PREFIX) and len(token) > len(FAKE_PASS_PREFIX):
            self._spent[token] = None
            while len(self._spent) > _FAKE_REMEMBERED:
                self._spent.popitem(last=False)
            stamp = datetime.fromtimestamp(now, UTC).isoformat().replace("+00:00", "Z")
            answer = {
                "success": True,
                "hostname": self._hostname,
                "action": action,
                "challenge_ts": stamp,
            }
        else:
            answer = {"success": False, "error-codes": ["invalid-input-response"]}
        check_answer(answer, hostname=self._hostname, action=action, now=now)


def build_verifier(settings: Settings) -> CaptchaVerifier | None:
    """The verifier for ``app.state.captcha``: ``None`` while Turnstile is off."""
    if not settings.turnstile_enabled:
        return None
    if settings.turnstile_fake:
        return FakeTurnstileVerifier(settings.turnstile_hostname)
    secret = settings.turnstile_secret_key
    if secret is None:  # Settings refuses this; kept so the type is exact
        raise ValueError("turnstile_enabled needs the turnstile_secret_key secret file")
    return TurnstileVerifier(secret, settings.turnstile_hostname)


def captcha_config(settings: Settings) -> dict[str, object] | None:
    """What ``GET /api/auth/session`` tells the page: the site key, ``fake`` or ``None`` (off)."""
    if not settings.turnstile_enabled:
        return None
    if settings.turnstile_fake:
        return {"site_key": None, "fake": True}
    return {"site_key": settings.turnstile_site_key, "fake": False}


def _remoteip(ip: str) -> str | None:
    address = parse_address(ip)
    return None if address is None else str(address)


async def require_captcha(request: Request, action: CaptchaAction, token: object) -> None:
    """The Turnstile check of an anonymous auth form (ADR 0025 section 2, steps 3 and 4): first in
    the handler after the body's shape, before any account lookup, account-scoped bucket, argon2,
    mail or session. Off (``CLASSBEAT_TURNSTILE_ENABLED`` false): nothing happens; in prod that is
    the owner's emergency degrade, so every protected request logs a warning."""
    settings: Settings = request.app.state.settings
    if not settings.turnstile_enabled:
        if settings.environment == "prod":
            logger.warning("captcha check is off: action=%s", action)
        return
    ip = request_ip(request)
    await reserve(request, [Bucket(CAPTCHA_IP, bucket_ip(ip))])
    verifier: CaptchaVerifier | None = getattr(request.app.state, "captcha", None)
    try:
        problem = token_problem(token)
        if problem is not None or not isinstance(token, str):
            raise CaptchaFailed(problem or "malformed")
        if verifier is None:  # on but not wired: never let a request through
            raise CaptchaUnavailable("not_configured", misconfigured=True)
        await verifier.verify(token, action, _remoteip(ip))
    except CaptchaFailed as refused:
        logger.warning("captcha refused: action=%s why=%s", action, refused.why)
        raise HTTPException(status_code=400, detail=FAILED) from None
    except CaptchaUnavailable as refused:
        level = logging.ERROR if refused.misconfigured else logging.WARNING
        logger.log(level, "captcha refused: action=%s why=%s", action, refused.why)
        raise HTTPException(
            status_code=503, detail=UNAVAILABLE, headers={"Retry-After": RETRY_AFTER}
        ) from None
