"""``POST /oidc/token``, ``GET /oidc/jwks`` and ``GET /.well-known/openid-configuration`` (ADR
0034 section 1).

The token endpoint is a server-to-server call: it is exempt from the Origin and CSRF checks
(``auth.middleware.CSRF_EXEMPT_PATHS``, amending ADR 0003 section 4); the client secret takes
their place. Client authentication is ``client_secret_basic`` only: no ``Authorization: Basic``,
a wrong client or secret, or a secret in the body is ``401 invalid_client`` and writes
``oidc_client_auth_failed``. The body is a form of at most 8 KiB. The code is redeemed (and so
burnt) first; it then counts only if the client, the redirect address, the expiry and the PKCE
verifier match and the user is still active. The answer is ``id_token``, ``token_type: Bearer``,
``expires_in: 300`` and an ``access_token`` that is random, stored nowhere and grants nothing.
Every answer, a 429 included, sends ``Cache-Control: no-store``.
"""

import binascii
import hashlib
import hmac
import logging
import re
from base64 import b64decode, urlsafe_b64encode
from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from classbeat.auth.deps import request_ip
from classbeat.auth.events import record_event
from classbeat.auth.flowtools import limiter, reserve
from classbeat.auth.keys import new_token
from classbeat.auth.oidc.code_store import RedeemedCode, redeem_code
from classbeat.auth.oidc.flow import NO_STORE, provider_of
from classbeat.auth.oidc.grant_store import mark_issued, person
from classbeat.auth.oidc.idtoken_calc import ACCESS_TOKEN_SECONDS, discovery, id_token_claims
from classbeat.auth.oidc.limits import (
    client_warning_buckets,
    token_failure_buckets,
    token_global_buckets,
)
from classbeat.auth.oidc.provider import Provider
from classbeat.auth.oidc.subject_calc import pairwise_subject
from classbeat.auth.ratelimit import RateLimited
from classbeat.db import system_session

logger = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)
BODY_MAX = 8 * 1024
_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_FORM = "application/x-www-form-urlencoded"
# The public documents: the player caches the key set itself (24 hours, ADR 0034 section 1).
_PUBLIC_CACHE = {"Cache-Control": "public, max-age=300"}


class _Refused(Exception):
    def __init__(self, error: str, status: int = 400) -> None:
        super().__init__(error)
        self.error, self.status = error, status


def _answer(body: dict[str, object], status: int = 200) -> JSONResponse:
    headers = {**NO_STORE, "Pragma": "no-cache"}
    if status == 401:
        headers["WWW-Authenticate"] = 'Basic realm="classbeat"'
    return JSONResponse(body, status_code=status, headers=headers)


async def _form(request: Request) -> dict[str, list[str]]:
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != _FORM:
        raise _Refused("invalid_request")
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > BODY_MAX:
            raise _Refused("invalid_request", 413)
    try:
        return parse_qs(raw.decode("ascii"), keep_blank_values=True, strict_parsing=False)
    except UnicodeDecodeError:
        raise _Refused("invalid_request") from None


def _single(form: dict[str, list[str]], name: str) -> str | None:
    values = form.get(name)
    if values is None:
        return None
    if len(values) != 1:
        raise _Refused("invalid_request")
    return values[0]


def _client_ok(request: Request, provider: Provider, form: dict[str, list[str]]) -> bool:
    """``client_secret_basic`` (RFC 6749 section 2.3.1) and nothing else."""
    if "client_secret" in form or "client_assertion" in form:
        return False
    scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "basic" or not encoded or len(encoded) > 512:
        return False
    try:
        decoded = b64decode(encoded, validate=True).decode("ascii")
    except (binascii.Error, UnicodeDecodeError):
        return False
    client_id, colon, secret = decoded.partition(":")
    matches = provider.secret_matches(unquote(secret))  # always computed: no early answer
    body_client = form.get("client_id")
    same_client = body_client is None or body_client == [provider.client.client_id]
    same_id = unquote(client_id) == provider.client.client_id
    return bool(colon) and same_id and same_client and matches


def _pkce_ok(verifier: str | None, challenge: str) -> bool:
    if verifier is None or not _VERIFIER.fullmatch(verifier):
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(expected, challenge)


async def _client_failed(request: Request, provider: Provider) -> None:
    async with system_session(request.app.state.session_factory) as db:
        await record_event(db, "oidc_client_auth_failed", ip=request_ip(request))
    counted = await limiter(request).begin(client_warning_buckets(provider.client.client_id))
    if not counted.allowed:
        logger.warning("oidc client authentication failures above 100 in an hour")


async def _redeem(request: Request, provider: Provider, form: dict[str, list[str]]) -> str:
    """The signed ID token, or ``_Refused``."""
    if _single(form, "grant_type") != "authorization_code":
        raise _Refused("unsupported_grant_type")
    factory = request.app.state.session_factory
    async with system_session(factory) as db:
        found = await redeem_code(db, _single(form, "code") or "")
    if not isinstance(found, RedeemedCode):
        async with system_session(factory) as db:
            await record_event(
                db, "oidc_code_refused", user_id=found.user_id, ip=request_ip(request),
                detail={"reason": found.reason},
            )  # fmt: skip
        raise _Refused("invalid_grant")
    reason = (
        "expired" if found.expired
        else "client" if found.client_id != provider.client.client_id
        else "redirect_uri" if _single(form, "redirect_uri") != found.redirect_uri
        else "pkce" if not _pkce_ok(_single(form, "code_verifier"), found.code_challenge)
        else None
    )  # fmt: skip
    async with system_session(factory) as db:
        user = await person(db, found.user_id) if reason is None else None
        if reason is None and (user is None or not user.active):
            reason = "user_inactive"
        if reason is None and user is not None:
            await mark_issued(db, found.user_id, provider.client.client_id)
            await record_event(db, "oidc_signed_in", user_id=found.user_id, ip=request_ip(request))
    if reason is not None or user is None:  # its own transaction: a refusal must stay written
        async with system_session(factory) as db:
            await record_event(
                db, "oidc_code_refused", user_id=found.user_id, ip=request_ip(request),
                detail={"reason": reason or "user_inactive"},
            )  # fmt: skip
        raise _Refused("invalid_grant")
    claims = id_token_claims(
        issuer=provider.issuer,
        client_id=provider.client.client_id,
        subject=pairwise_subject(provider.pairwise_key, provider.client.client_id, found.user_id),
        now=datetime.now(UTC),
        auth_time=found.auth_time,
        nonce=found.nonce,
        email=user.email,
        email_verified=user.email_verified,
    )
    return provider.signer.sign(claims)


@router.post("/oidc/token")
async def token(request: Request) -> JSONResponse:
    provider = provider_of(request)
    try:
        await reserve(request, token_global_buckets())
        attempt = await reserve(request, token_failure_buckets(request_ip(request)))
    except RateLimited as limited:
        answer = _answer({"error": "slow_down"}, 429)
        answer.headers["Retry-After"] = str(limited.retry_after)
        return answer
    try:
        form = await _form(request)
        if not _client_ok(request, provider, form):
            await _client_failed(request, provider)
            return _answer({"error": "invalid_client"}, 401)
        id_token = await _redeem(request, provider, form)
    except _Refused as refused:
        return _answer({"error": refused.error}, refused.status)
    await attempt.succeed()
    return _answer(
        {
            "id_token": id_token,
            "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_SECONDS,
            "access_token": new_token(),  # stored nowhere, grants nothing
        }
    )


@router.get("/oidc/jwks")
async def jwks(request: Request) -> JSONResponse:
    provider = provider_of(request)
    keys = provider.signer.jwks(datetime.now(UTC))
    return JSONResponse(keys, headers=_PUBLIC_CACHE)


@router.get("/.well-known/openid-configuration")
async def configuration(request: Request) -> JSONResponse:
    provider = provider_of(request)
    return JSONResponse(discovery(provider.issuer), headers=_PUBLIC_CACHE)
