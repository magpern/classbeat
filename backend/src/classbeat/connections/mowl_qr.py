"""MOWL connect by QR code (ADR 0024, #647): the user scans a code shown on the desktop site with
the MOWL phone app, and MOWL's broker hands the new session token to the ticket's creator.

1. ``request_ticket``: ``GET v1/qr/create/authentication`` (anonymous, through the library's
   ``call``, so the web's urllib allowlist applies). The answer holds the topic key (``Token``)
   and per-ticket broker credentials; ``parse_ticket`` checks every field it uses and keeps them
   in a ``QrTicket`` that never shows them (no ``repr``, no pickling, no copy).
2. ``fetch_image``: the public QR image from ``media.mowl.com``, fetched here and served from our
   origin (the CSP allows no other image host). Read raw, at most ``IMAGE_MAX_BYTES``, identity
   encoding only, and its type from its first bytes (PNG, JPEG, GIF or WebP; never SVG).
3. ``QrWaits``: one wait per user, in this process's memory. Its task opens the TLS connection to
   the broker (``classbeat.egress.mowl_mqtt_open``: 8883 only, verified, no fallback), subscribes
   to ``qrticket/<Token>`` and keeps the one 36-character payload, the session token, until the
   user's next status poll takes it (``QrWait.take_token``). A wait is bound to the user **and the
   session** that started it (which passed the recent-authentication check); only a poll from
   that same live session finds it and stores the token as the password flow does
   (``repository.save``). A wait ends at the ticket's 15 minutes, on a new ticket for the same
   user, on the page's cancel and on shutdown; a token nobody took within
   ``SCANNED_KEEP_SECONDS`` is dropped, and a finished wait is forgotten after
   ``FINISHED_KEEP_SECONDS``.

The topic key, the broker password and the session token are never logged, put in an exception,
a URL, a response or the database other than the encrypted connection row. Logs carry the
outcome word and a category only.
"""

import asyncio
import contextlib
import logging
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal, NoReturn
from uuid import UUID

import httpx
from mowl import api as mowl_api

from classbeat import egress
from classbeat.connections import mowl_mqtt
from classbeat.connections.errors import ConnectionFailure, run_library_call

logger = logging.getLogger(__name__)

TICKET_PATH: Final = "v1/qr/create/authentication"
# MOWL's ticket lives 15 minutes (``DateExpired``); the wait never outlives that on our clock.
TICKET_SECONDS: Final = 15 * 60
TOPIC_PREFIX: Final = "qrticket/"
IMAGE_URL: Final = f"https://{egress.MOWL_QR_IMAGE_HOST}/qr/ticket/{{}}"
IMAGE_MAX_BYTES: Final = 256 * 1024
MQTT_FIELD_MAX: Final = 256
# Waits in this process at once (each holds one broker connection). Over it, a start is refused.
MAX_WAITS: Final = 100
# How long a scanned token waits in memory for the page's next poll (it polls every few seconds).
SCANNED_KEEP_SECONDS: Final = 60.0
# How long a finished wait stays readable, so the page's next poll still learns its outcome.
FINISHED_KEEP_SECONDS: Final = 120.0
_GUID = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
_IMAGE_TYPES: Final = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

State = Literal["waiting", "storing", "scanned", "connected", "expired", "failed", "cancelled"]


def is_guid(value: object) -> bool:
    return isinstance(value, str) and _GUID.fullmatch(value) is not None


class TicketRefused(ValueError):
    """MOWL's ticket did not have the shape this flow relies on. No text (the answer holds the
    topic key and the broker password)."""

    def __init__(self) -> None:
        super().__init__("unexpected ticket")


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class QrTicket:
    """What the wait needs from a ticket. ``key`` (the topic's GUID) and ``password`` (the
    broker's) are secrets: never shown, serialized or copied."""

    ticket_id: int
    key: str
    username: str
    password: str

    @property
    def topic(self) -> str:
        return TOPIC_PREFIX + self.key

    @property
    def image_url(self) -> str:
        return IMAGE_URL.format(self.ticket_id)

    def __repr__(self) -> str:
        return "QrTicket(...)"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("a QR ticket is never serialized")

    def __reduce_ex__(self, protocol: Any) -> NoReturn:
        raise TypeError("a QR ticket is never serialized")

    def __copy__(self) -> NoReturn:
        raise TypeError("a QR ticket is never copied")

    def __deepcopy__(self, memo: Any) -> NoReturn:
        raise TypeError("a QR ticket is never copied")


def _short_text(value: object) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= MQTT_FIELD_MAX:
        raise TicketRefused
    if not value.isascii() or not value.isprintable():
        raise TicketRefused
    return value


def parse_ticket(data: object) -> QrTicket:
    """The ticket from MOWL's ``Data``. Refused (``TicketRefused``) unless the ticket id is a
    positive integer, ``Token`` a GUID, ``QrImageUrl`` exactly ours for that id, and the broker
    MOWL names is ``egress.MOWL_MQTT_HOST`` with TLS on ``egress.MOWL_MQTT_TLS_PORT`` and flow
    version 1. ``DisableSSL`` is never read: TLS is always on."""
    if not isinstance(data, Mapping):
        raise TicketRefused
    ticket: Mapping[str, Any] = data  # pyright: ignore[reportUnknownVariableType]
    ticket_id = ticket.get("QrTicketID")
    if not isinstance(ticket_id, int) or isinstance(ticket_id, bool) or not 0 < ticket_id < 2**53:
        raise TicketRefused
    key = ticket.get("Token")
    if not isinstance(key, str) or not is_guid(key):
        raise TicketRefused
    if ticket.get("QrImageUrl") != IMAGE_URL.format(ticket_id):
        raise TicketRefused
    info = ticket.get("MQTTConnectionInfo")
    if not isinstance(info, Mapping):
        raise TicketRefused
    broker: Mapping[str, Any] = info  # pyright: ignore[reportUnknownVariableType]
    host = broker.get("MQTTServerAddress")
    if not isinstance(host, str) or host.lower() != egress.MOWL_MQTT_HOST:
        raise TicketRefused
    if broker.get("MQTTServerSSLPort") != egress.MOWL_MQTT_TLS_PORT:
        raise TicketRefused
    if broker.get("MQTTFlowVersion") != 1:
        raise TicketRefused
    return QrTicket(
        ticket_id=ticket_id,
        key=key,
        username=_short_text(broker.get("Username")),
        password=_short_text(broker.get("Password")),
    )


def _create_ticket() -> QrTicket:
    """The library's anonymous call (no ``authorization``: ``creds`` is None), then the check.
    Synchronous; runs in a worker thread."""
    return parse_ticket(mowl_api.call(None, "GET", TICKET_PATH))


# What a ticket failure may say: MOWL is busy, unreachable, or answered unexpectedly.
_TICKET_CATEGORIES = frozenset({"rate_limited", "upstream_unavailable"})


async def request_ticket() -> QrTicket:
    """A new ticket. ``ConnectionFailure``: ``rate_limited``, ``upstream_unavailable`` or
    ``unknown`` (anything else, a refused shape included)."""
    failure: ConnectionFailure | None = None
    try:
        return await run_library_call(_create_ticket, sign_in=False)
    except ConnectionFailure as error:
        failure = error
    except egress.OutboundBlocked:
        failure = ConnectionFailure("unknown")
    category = failure.category if failure.category in _TICKET_CATEGORIES else "unknown"
    raise ConnectionFailure(category, failure.retry_after)


@dataclass(frozen=True, slots=True)
class QrImage:
    content: bytes
    content_type: str


def image_type(content: bytes) -> str | None:
    """The image type from the first bytes: PNG, JPEG, GIF or WebP; anything else is None."""
    for magic, kind in _IMAGE_TYPES:
        if content.startswith(magic):
            return kind
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


async def _read_image(url: str) -> QrImage | str:
    """The image, or the failure category."""
    headers = {"User-Agent": mowl_api.HEADERS["User-Agent"], "Accept": "image/*"}
    async with (
        egress.mowl_qr_image_async_client() as client,
        client.stream("GET", url, headers=headers) as response,
    ):
        if response.status_code != 200:
            if response.status_code == 429 or response.status_code >= 500:
                return "upstream_unavailable"
            return "unknown"
        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
            return "unknown"
        body = bytearray()
        async for chunk in response.aiter_raw():
            body += chunk
            if len(body) > IMAGE_MAX_BYTES:
                return "unknown"
    kind = image_type(bytes(body))
    return "unknown" if kind is None else QrImage(bytes(body), kind)


async def fetch_image(ticket: QrTicket) -> QrImage:
    """The ticket's QR image. ``ConnectionFailure``: ``upstream_unavailable`` (unreachable,
    timed out, 429 or 5xx) or ``unknown`` (another status, an encoding, too large, not an image
    type we serve, or a refused request)."""
    category = "unknown"
    try:
        outcome = await _read_image(ticket.image_url)
    except egress.OutboundBlocked:
        outcome = "unknown"
    except (httpx.HTTPError, TimeoutError, OSError):
        outcome = "upstream_unavailable"
    if isinstance(outcome, QrImage):
        return outcome
    category = outcome
    logger.warning("mowl qr image failed", extra={"category": category})
    raise ConnectionFailure(category)


@dataclass(eq=False, repr=False)
class QrWait:
    """One user's wait. ``id`` is ours (random, not MOWL's ticket id); the token, once the phone
    scanned, is held only until ``take_token``."""

    id: str
    user_id: UUID
    session_id: UUID
    image: QrImage
    expires_at: datetime
    deadline: float  # the event loop's clock
    state: State = "waiting"
    reason: str | None = None
    finished_at: float | None = None
    task: "asyncio.Task[None] | None" = None
    _token: str | None = field(default=None)

    def __repr__(self) -> str:
        return f"QrWait(state={self.state!r})"

    def take_token(self) -> str | None:
        """The token, once; ``state`` becomes ``storing`` so a parallel poll gets nothing."""
        token, self._token = self._token, None
        if token is not None:
            self.state = "storing"
        return token

    def finish(self, state: State, reason: str | None = None) -> None:
        self.state, self.reason = state, reason
        self._token = None if state != "scanned" else self._token
        self.finished_at = asyncio.get_running_loop().time()


class QrWaits:
    """The waits of this web process, one per user (``app.state.mowl_qr``)."""

    def __init__(self) -> None:
        self._by_user: dict[UUID, QrWait] = {}

    def __len__(self) -> int:
        return len(self._by_user)

    def _prune(self, now: float) -> None:
        for user_id, wait in list(self._by_user.items()):
            if wait.finished_at is not None and now - wait.finished_at > FINISHED_KEEP_SECONDS:
                del self._by_user[user_id]

    def forget(self, wait: QrWait) -> None:
        """Drop ``wait`` if it is still its user's current one."""
        if self._by_user.get(wait.user_id) is wait:
            del self._by_user[wait.user_id]

    def start(self, user_id: UUID, session_id: UUID, ticket: QrTicket, image: QrImage) -> QrWait:
        """A new wait for ``user_id``, bound to ``session_id``; the user's earlier one is
        cancelled first. Refused (``ConnectionFailure("rate_limited")``) when ``MAX_WAITS`` are
        already running."""
        loop = asyncio.get_running_loop()
        now = loop.time()
        self._prune(now)
        self.cancel_user(user_id)
        if sum(1 for w in self._by_user.values() if w.finished_at is None) >= MAX_WAITS:
            raise ConnectionFailure("rate_limited", 60)
        wait = QrWait(
            id=secrets.token_urlsafe(24),
            user_id=user_id,
            session_id=session_id,
            image=image,
            expires_at=datetime.now(UTC) + timedelta(seconds=TICKET_SECONDS),
            deadline=now + TICKET_SECONDS,
        )
        wait.task = asyncio.create_task(_run(wait, ticket, self), name="mowl-qr-wait")
        self._by_user[user_id] = wait
        return wait

    def get(self, user_id: UUID, session_id: UUID, wait_id: str) -> QrWait | None:
        """The user's wait with this id, started by this session; another user's, another
        session's or an unknown id is None."""
        wait = self._by_user.get(user_id)
        if wait is None or wait.session_id != session_id:
            return None
        if not secrets.compare_digest(wait.id.encode(), wait_id.encode()):
            return None
        return wait

    def cancel_user(self, user_id: UUID) -> bool:
        wait = self._by_user.get(user_id)
        if wait is None:
            return False
        if wait.task is not None and not wait.task.done():
            wait.task.cancel()
        if wait.finished_at is None or wait.state == "scanned":
            wait.finish("cancelled")
        return True

    def cancel(self, user_id: UUID, session_id: UUID, wait_id: str) -> bool:
        if self.get(user_id, session_id, wait_id) is None:
            return False
        return self.cancel_user(user_id)

    async def close(self) -> None:
        """Cancel every wait (the app's shutdown) and drop them."""
        tasks = [w.task for w in self._by_user.values() if w.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._by_user.clear()


async def _receive(ticket: QrTicket) -> bytes:
    reader, writer = await egress.mowl_mqtt_open()
    try:
        return await mowl_mqtt.wait_for_message(
            reader,
            writer,
            client_id=mowl_mqtt.client_id(),
            username=ticket.username,
            password=ticket.password,
            topic=ticket.topic,
        )
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            async with asyncio.timeout(2):
                await writer.wait_closed()


def _token_of(payload: bytes) -> str | None:
    """The payload is the session token itself: 36 bytes of plain text, a GUID."""
    if len(payload) != 36:
        return None
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError:
        return None
    return text if is_guid(text) else None


async def _run(wait: QrWait, ticket: QrTicket, waits: QrWaits) -> None:
    """The wait's task: receive the token or end ``expired``, ``failed`` or ``cancelled``; then
    drop a token nobody took and forget the wait."""
    await _wait(wait, ticket)
    if wait.state == "scanned":
        await asyncio.sleep(SCANNED_KEEP_SECONDS)
        if wait.state == "scanned":  # no poll took it: it is never stored
            wait.finish("expired")
            logger.info("mowl qr wait ended", extra={"outcome": "not_taken"})
    await asyncio.sleep(FINISHED_KEEP_SECONDS)
    waits.forget(wait)


async def _wait(wait: QrWait, ticket: QrTicket) -> None:
    """Receive the token (``scanned``) or end ``expired`` or ``failed``."""
    loop = asyncio.get_running_loop()
    payload: bytes | None = None
    category: str | None = None
    try:
        async with asyncio.timeout_at(wait.deadline):
            payload = await _receive(ticket)
    except TimeoutError:
        category = "expired" if loop.time() >= wait.deadline else "upstream_unavailable"
    except egress.OutboundBlocked:
        category = "unknown"
    except (mowl_mqtt.MqttError, OSError):  # ssl.SSLError is an OSError
        category = "upstream_unavailable"
    except Exception:
        category = "unknown"
    # Outside the except blocks: no exception (and no frame holding the ticket) is kept.
    if wait.finished_at is not None:  # cancelled meanwhile
        return
    token = None if payload is None else _token_of(payload)
    if payload is not None and token is None:
        category = "unknown"
    if token is not None:
        wait._token = token  # pyright: ignore[reportPrivateUsage]
        wait.finish("scanned")
        logger.info("mowl qr wait ended", extra={"outcome": "scanned"})
        return
    state: State = "expired" if category == "expired" else "failed"
    wait.finish(state, None if state == "expired" else category)
    logger.info("mowl qr wait ended", extra={"outcome": state, "category": category})
