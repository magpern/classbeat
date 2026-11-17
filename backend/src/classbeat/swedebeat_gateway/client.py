"""The SwedeBeat gateway calls (ADR 0035 sections 3, 5 and 6; ADR 0036 section 8): exactly
``login``, ``session``, ``match`` and ``download_track``, nothing else (the playlist writes are
``writes``). Each takes a client of
``classbeat.egress_gateway`` (one per call) and the client key; the bearer goes only into
``session``, ``match`` and ``download_track``.

- ``login``: one ``POST /auth/login`` (``login_policy.login_body``) with ``X-API-Key``,
  ``Content-Type`` and ``Accept``; the answer through ``login_policy.parse_login``.
- ``session``: one ``GET /auth/session`` with ``X-API-Key``, ``Authorization: Bearer`` and
  ``Accept``: is the bearer still good (the match's 401 confirmation, SG-2).
- ``match``: one ``POST /catalog/match`` (``match_policy.match_body``, tracks mode only) with
  ``X-API-Key``, ``Authorization: Bearer``, ``Content-Type`` and ``Accept``; the answer through
  ``match_policy.parse_match``.
- ``download_track``: one ``GET /tracks/{reference}/download`` (``track_policy``; no query, so
  never the preview) with ``X-API-Key``, ``Authorization: Bearer`` and ``Accept: audio/mpeg``,
  streamed into a new file (``download_file``). Its client is opened with
  ``egress_gateway.DOWNLOAD_SECONDS`` (120 s, the body included).

Every answer is read raw with identity encoding only and a cap (``answer.read_capped``: login and
session 16 KB, match 1 MB). The category comes from ``status_policy``: a ``GatewayFailure`` with
it (and ``Retry-After`` for ``rate_limited``), a timeout, the deadline or a refused connection
being ``upstream_unavailable``. A redirect is ``unknown`` for the login and ``OutboundBlocked``
(``after_request``) for the session, the match and the download. A refused bearer
(``bearer_refused``) is the caller's to confirm with one ``session`` (ADR 0035 section 5,
``swedebeat_match.confirm_policy``), for the download as for the match.

**Nothing secret leaves in an error.** No password, key or bearer is ever in an exception, a log
line or a ``repr`` (``LoginAnswer`` hides its bearer). Nothing here logs. ``GatewayFailure`` and
the re-raised ``OutboundBlocked`` are raised after their ``except`` block, so they chain nothing:
httpx's errors hold the request, and the request holds the headers.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import httpx
from pydantic import SecretStr

from classbeat.connections.errors import retry_after_of
from classbeat.egress import OutboundBlocked, blocked
from classbeat.swedebeat_gateway.answer import read_capped
from classbeat.swedebeat_gateway.download_file import (
    Download,
    Refused,
    close,
    create,
    discard,
    receive,
)
from classbeat.swedebeat_gateway.login_policy import (
    BEARER_MAX,
    BEARER_MIN,
    LOGIN_ANSWER_MAX,
    SESSION_ANSWER_MAX,
    LoginAnswer,
    login_body,
    parse_login,
    parse_session,
)
from classbeat.swedebeat_gateway.match_policy import (
    MATCH_ANSWER_MAX,
    Item,
    match_body,
    parse_match,
)
from classbeat.swedebeat_gateway.status_policy import (
    call_category,
    is_unknown_client,
    login_category,
)
from classbeat.swedebeat_gateway.track_policy import (
    AUDIO_MPEG,
    DOWNLOAD_MAX,
    download_category,
    download_path,
)

__all__ = [
    "Download",
    "GatewayFailure",
    "Item",
    "LoginAnswer",
    "download_track",
    "login",
    "match",
    "session",
]

LOGIN_PATH: Final = "/auth/login"
SESSION_PATH: Final = "/auth/session"
MATCH_PATH: Final = "/catalog/match"
# A 401's body is read for its ``detail`` only (``unknown client`` or not).
ERROR_ANSWER_MAX: Final = 16 * 1024
_JSON: Final = "application/json"


class GatewayFailure(Exception):
    """A call that gave no usable answer. ``args == (category,)``; ``retry_after`` is the
    gateway's (or a proxy's) whole-second ``Retry-After`` of a ``rate_limited`` answer."""

    def __init__(self, category: str, retry_after: int | None = None) -> None:
        super().__init__(category)
        self.category = category
        self.retry_after = retry_after if category == "rate_limited" else None


@dataclass(frozen=True, slots=True)
class _Answer:
    status: int
    body: bytes | None = field(repr=False)  # a login's holds the bearer
    retry_after: int | None = None


def _headers(key: SecretStr, bearer: str | None, *, sends_body: bool) -> dict[str, str]:
    headers = {
        "Accept": _JSON,
        "Accept-Encoding": "identity",
        "X-API-Key": key.get_secret_value(),
    }
    if sends_body:
        headers["Content-Type"] = _JSON
    if bearer is not None:
        if not (BEARER_MIN <= len(bearer) <= BEARER_MAX and all("!" <= c <= "~" for c in bearer)):
            raise ValueError("the bearer is 16 to 4096 printable ASCII characters")
        headers["Authorization"] = f"Bearer {bearer}"
    return headers


async def _exchange(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    headers: dict[str, str],
    content: bytes | None,
    cap: int,
) -> _Answer:
    """One request; its status, the body of a 200 (or a 401's, for ``detail``) and a 429's
    ``Retry-After``. ``OutboundBlocked`` and ``GatewayFailure`` leave unchained."""
    status: int | None = None
    body: bytes | None = None
    retry_after: int | None = None
    refusal: tuple[str, bool] | None = None
    try:
        async with client.stream(method, path, headers=headers, content=content) as response:
            status = response.status_code
            if status == 200:
                body = await read_capped(response, cap)
            elif status == 401:
                body = await read_capped(response, ERROR_ANSWER_MAX)
            elif status == 429:
                retry_after = retry_after_of(response)
    except OutboundBlocked as refused:
        refusal = (refused.host, refused.after_request)
    except (httpx.HTTPError, TimeoutError, OSError):
        status = None
    if refusal is not None:
        raise OutboundBlocked(refusal[0], after_request=refusal[1])
    if status is None:
        raise GatewayFailure("upstream_unavailable")
    return _Answer(status, body, retry_after)


async def login(
    client: httpx.AsyncClient,
    key: SecretStr,
    email: str,
    password: str,
    device_id: str,
    *,
    force: bool,
) -> LoginAnswer:
    """Sign in through the gateway; the bearer and its lifetime, or ``GatewayFailure``."""
    content = login_body(email, password, device_id, force=force)
    headers = _headers(key, None, sends_body=True)
    answer: _Answer | None = None
    try:
        answer = await _exchange(client, "POST", LOGIN_PATH, headers, content, LOGIN_ANSWER_MAX)
    except OutboundBlocked:
        answer = None  # a redirect to another origin: ``unknown``, as a same-origin one
    if answer is None:
        raise GatewayFailure("unknown")  # outside the except block: nothing chained
    parsed = parse_login(answer.body) if answer.status == 200 else None
    unknown_client = answer.status == 401 and is_unknown_client(answer.body)
    category = login_category(
        answer.status, unknown_client=unknown_client, valid_body=parsed is not None
    )
    if category is not None or parsed is None:
        raise GatewayFailure(category or "unknown", answer.retry_after)
    return parsed


def _call_failure(
    client: httpx.AsyncClient, answer: _Answer, *, valid_body: bool
) -> GatewayFailure | OutboundBlocked | None:
    unknown_client = answer.status == 401 and is_unknown_client(answer.body)
    category = call_category(answer.status, unknown_client=unknown_client, valid_body=valid_body)
    if category == "redirect":
        # A same-origin 3xx came back as an answer: refused, never followed.
        return blocked(client.base_url.host, after_request=True)
    return None if category is None else GatewayFailure(category, answer.retry_after)


async def session(client: httpx.AsyncClient, key: SecretStr, bearer: str) -> None:
    """``None`` when the gateway still takes ``bearer``; ``GatewayFailure`` (``bearer_refused``
    when it does not) or ``OutboundBlocked`` otherwise."""
    headers = _headers(key, bearer, sends_body=False)
    answer = await _exchange(client, "GET", SESSION_PATH, headers, None, SESSION_ANSWER_MAX)
    failure = _call_failure(client, answer, valid_body=parse_session(answer.body))
    if failure is not None:
        raise failure


async def match(
    client: httpx.AsyncClient, key: SecretStr, bearer: str, tracks: Sequence[tuple[str, str]]
) -> list[Item]:
    """One item per ``(title, artist)`` in ``tracks``, in order; ``GatewayFailure`` or
    ``OutboundBlocked`` otherwise."""
    content = match_body(tracks)
    headers = _headers(key, bearer, sends_body=True)
    answer = await _exchange(client, "POST", MATCH_PATH, headers, content, MATCH_ANSWER_MAX)
    items = parse_match(answer.body, len(tracks)) if answer.status == 200 else None
    failure = _call_failure(client, answer, valid_body=items is not None)
    if failure is not None:
        raise failure
    if items is None:  # pragma: no cover - a 200 without items is a failure above
        raise GatewayFailure("invalid_response")
    return items


async def download_track(
    client: httpx.AsyncClient,
    key: SecretStr,
    bearer: str,
    reference: str,
    dest_path: Path,
    *,
    max_bytes: int = DOWNLOAD_MAX,
) -> Download:
    """Stream the track ``reference`` into the new file ``dest_path`` (its directory private:
    ``download_file``); its sha256 and size. ``ValueError`` before anything is sent for a bad
    reference, bearer or destination; otherwise ``GatewayFailure`` or ``OutboundBlocked``, and no
    file is left behind. ``max_bytes`` (at most ``DOWNLOAD_MAX``) holds while reading, whatever
    ``Content-Length`` says."""
    if not 0 < max_bytes <= DOWNLOAD_MAX:
        raise ValueError("the download cap is at most 60 MB")
    path = download_path(reference)
    headers = _headers(key, bearer, sends_body=False) | {"Accept": AUDIO_MPEG}
    out = create(dest_path)  # before the request: a bad destination sends nothing
    outcome: Download | Refused | None = None
    refusal: tuple[str, bool] | None = None
    try:
        async with client.stream("GET", path, headers=headers) as response:
            outcome = await receive(response, out, max_bytes, ERROR_ANSWER_MAX)
    except OutboundBlocked as refused:
        refusal = (refused.host, refused.after_request)
    except (httpx.HTTPError, TimeoutError, OSError):
        outcome = None
    finally:  # a cancel too: no partial file is left behind
        if not close(out) and isinstance(outcome, Download):
            outcome = Refused(None, "internal")  # the last bytes may not be on disk
        if not isinstance(outcome, Download):
            discard(dest_path)  # the file this call made, never another
    if refusal is not None:
        raise OutboundBlocked(refusal[0], after_request=refusal[1])
    if outcome is None:
        raise GatewayFailure("upstream_unavailable")
    if isinstance(outcome, Download):
        return outcome
    if outcome.status is None:
        raise GatewayFailure(outcome.category)
    unknown_client = outcome.status == 401 and is_unknown_client(outcome.body)
    category = download_category(outcome.status, unknown_client=unknown_client)
    if category == "redirect":
        raise blocked(client.base_url.host, after_request=True)
    raise GatewayFailure(category or "invalid_response", outcome.retry_after)
