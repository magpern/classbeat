"""Fixed-window rate limits in Postgres (ADR 0003 section 9). There is no Redis.

Every attempt first takes a slot in each of its buckets with one atomic upsert, each in its own
committed transaction, and is refused above the limit before any password check runs, so
parallel attempts cannot overshoot. Sign-in, re-auth and MFA limits count failures only: the
success releases what it reserved (``Attempt.succeed``) and never touches a newer window.
Bucket keys are an HMAC of ``(action, scope, value, window)``: no email or IP in the clear.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import Float, and_, delete, exists, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.auth.clientip import bucket_ip
from classbeat.auth.keys import Keys, hmac_digest
from classbeat.auth.tables import rate_limits
from classbeat.db import system_session

if TYPE_CHECKING:
    from classbeat.config import Settings


@dataclass(frozen=True, slots=True)
class Limit:
    """``max_count`` slots per fixed window of ``window_seconds``."""

    action: str
    scope: str  # "pair" (account and IP), "account" or "ip"
    max_count: int
    window_seconds: int


# The table of ADR 0003 section 9.
SIGN_IN_PAIR = Limit("sign-in", "pair", 10, 15 * 60)
SIGN_IN_ACCOUNT = Limit("sign-in", "account", 50, 3600)
SIGN_IN_IP = Limit("sign-in", "ip", 30, 15 * 60)
MFA_PAIR = Limit("mfa", "pair", 5, 5 * 60)
MFA_ACCOUNT = Limit("mfa", "account", 20, 3600)
MFA_IP = Limit("mfa", "ip", 20, 5 * 60)
SIGNUP_ADDRESS = Limit("sign-up", "account", 3, 3600)
SIGNUP_IP = Limit("sign-up", "ip", 5, 3600)
MAIL_PAIR = Limit("mail", "pair", 3, 3600)
MAIL_ACCOUNT = Limit("mail", "account", 10, 3600)
MAIL_IP = Limit("mail", "ip", 10, 3600)
OAUTH_IP = Limit("oauth", "ip", 30, 15 * 60)
PASSWORD_CHANGE = Limit("password-change", "account", 10, 3600)
# ADR 0004 section 4: provider connects (MOWL sign-in with a password) count requests, per
# classbeat user, per IP and per normalized provider email, so the route can't be used to test
# passwords against MOWL. A success releases its slots.
CONNECT_USER = Limit("connect", "account", 5, 15 * 60)
CONNECT_IP = Limit("connect", "ip", 20, 15 * 60)
CONNECT_PROVIDER_EMAIL = Limit("connect", "provider-email", 5, 15 * 60)
# ADR 0009 section 4: Spotify connect starts, per user and per IP and hour. A completed
# connection releases its slot (the callback, ``RateLimiter.release``).
SPOTIFY_START_USER = Limit("spotify-start", "account", 10, 3600)
SPOTIFY_START_IP = Limit("spotify-start", "ip", 30, 3600)
# ADR 0011 section 6: every /api/admin request, per admin and minute.
ADMIN_USER = Limit("admin", "account", 60, 60)
# ADR 0011 section 2: redeem-code attempts, per user and per IP and hour. Every attempt counts,
# a success too, so the route can't be used to try codes faster than this.
REDEEM_USER = Limit("redeem", "account", 10, 3600)
REDEEM_IP = Limit("redeem", "ip", 30, 3600)
# ADR 0022 section 6 (#653): the Mail page's "Send test to me", per admin and hour. Every request
# counts, a refused one too, so the page cannot be used to send mail faster than this.
ADMIN_MAIL_TEST = Limit("admin-mail-test", "account", 10, 3600)
# ADR 0014 section 2: the data export reads every owned table, so it is limited per user and
# hour (every request counts) and per IP.
EXPORT_USER = Limit("export", "account", 5, 3600)
EXPORT_IP = Limit("export", "ip", 20, 3600)
# SR-05 (#354): ``GET /api/music/mowl-playlists`` makes a MOWL call with the user's token on every
# request, and a GET can be triggered cross-site, so it is limited per user and per IP. Every
# request counts (a refusal comes before the connection is opened and before any MOWL call).
MOWL_PLAYLISTS_USER = Limit("mowl-playlists", "account", 30, 600)
MOWL_PLAYLISTS_IP = Limit("mowl-playlists", "ip", 60, 600)
# ADR 0016 section 3 (A2): ``GET /api/music/swedebeat-mixes`` is the same kind of read, with its
# own buckets so the two pickers don't share a count.
SWEDEBEAT_MIXES_USER = Limit("swedebeat-mixes", "account", 30, 600)
SWEDEBEAT_MIXES_IP = Limit("swedebeat-mixes", "ip", 60, 600)

# ADR 0013 section 9, amendment of 2026-11-03 (#537): ``GET /api/music/playlist-covers`` reads the
# user's Spotify playlists (up to 10 requests and a refresh on the user's own Spotify app) and can
# be triggered cross-site, so it has buckets of its own; every request counts.
PLAYLIST_COVERS_USER = Limit("playlist-covers", "account", 10, 600)
PLAYLIST_COVERS_IP = Limit("playlist-covers", "ip", 30, 600)
# ADR 0024 section 5 (#647): a MOWL QR ticket (one anonymous MOWL call, one image fetch and one
# broker connection of up to 15 minutes). Every start counts, a refused one too; none is released.
MOWL_QR_USER = Limit("mowl-qr", "account", 10, 15 * 60)
MOWL_QR_IP = Limit("mowl-qr", "ip", 30, 15 * 60)
# The cover read's cooldown after a Spotify 429: a dedicated bucket hash per user (not a fixed
# window). Each 429 inserts a row whose ``window_start`` is the instant that cooldown ends; the
# check asks whether any row's end is still in the future, so the latest end counts and a later,
# shorter Retry-After never shortens a running cooldown. The purge (windows over two hours old)
# removes each row two hours after it ended.
COVER_COOLDOWN_ACTION = "cover-cooldown"
COVER_COOLDOWN_MIN_SECONDS = 60
COVER_COOLDOWN_MAX_SECONDS = 3600

# Issue #577 (ADR 0003 section 9, amendment of 2026-11-05): the general limit on signed-in
# ``/api`` requests (``api_buckets``), so a script with a stolen session is slowed down. The
# counts come from the settings (``CLASSBEAT_API_*``); the actions, scopes and windows are fixed
# here. Every request counts, a refused one too. A bucket's key does not name its count, so
# changing a setting keeps the running windows.
#
# Never limited: the probes (``/api/health`` touches nothing; ``/api/ready`` runs only
# ``SELECT 1``, no owned data, and is exempt so an orchestrator's probe loop never fills a
# bucket), and signing out, so a user whose buckets a script has used up can still end every
# session, the script's included.
API_EXEMPT_PATHS = frozenset(
    {"/api/health", "/api/ready", "/api/auth/logout", "/api/auth/logout-all"}
)
# The reads a scraper wants (the user's classes, revisions, messages and history; the jobs and
# their drafts and streams): a tighter per-minute bucket of their own for safe methods.
API_READ_PREFIXES = ("/api/classes", "/api/jobs")
API_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

OnSuccess = Literal["decrement", "delete"]


@dataclass(frozen=True, slots=True)
class Bucket:
    """One counter an attempt reserves: a limit and the value it is counted for."""

    limit: Limit
    value: str
    on_success: OnSuccess = "decrement"


@dataclass(frozen=True, slots=True)
class Slot:
    bucket_hash: bytes
    window_start: datetime
    on_success: OnSuccess


@dataclass(slots=True)
class Attempt:
    """The result of reserving every bucket of one attempt."""

    allowed: bool
    retry_after: int  # seconds until the longest tripped window ends; 0 when allowed
    slots: list[Slot] = field(default_factory=lambda: list[Slot]())
    _factory: async_sessionmaker[AsyncSession] | None = None

    async def succeed(self) -> None:
        """Release a successful attempt: delete the buckets marked ``delete`` (all their
        windows) and decrement, by one, the exact rows reserved (the same ``window_start``)."""
        if self._factory is None or not self.slots:
            return
        async with system_session(self._factory) as session:
            for slot in self.slots:
                if slot.on_success == "delete":
                    await session.execute(
                        delete(rate_limits).where(rate_limits.c.bucket_hash == slot.bucket_hash)
                    )
                else:
                    await session.execute(
                        update(rate_limits)
                        .where(
                            and_(
                                rate_limits.c.bucket_hash == slot.bucket_hash,
                                rate_limits.c.window_start == slot.window_start,
                            )
                        )
                        .values(count=func.greatest(rate_limits.c.count - 1, 0))
                    )
        self.slots = []


def _window_start(window: int) -> Any:
    return func.to_timestamp(
        func.floor(func.extract("epoch", func.clock_timestamp()) / window) * window
    )


class RateLimiter:
    def __init__(self, factory: async_sessionmaker[AsyncSession], keys: Keys) -> None:
        self._factory = factory
        self._keys = keys

    async def release(self, buckets: Sequence[Bucket]) -> None:
        """Give back one slot of each bucket in its current window (never below zero): a
        success that completes in another request than the one that reserved (the Spotify
        callback releases its start's slot)."""
        async with system_session(self._factory) as session:
            for bucket in buckets:
                bucket_hash = self.bucket_hash(bucket.limit, bucket.value)
                await session.execute(
                    update(rate_limits)
                    .where(
                        and_(
                            rate_limits.c.bucket_hash == bucket_hash,
                            rate_limits.c.window_start
                            == _window_start(bucket.limit.window_seconds),
                        )
                    )
                    .values(count=func.greatest(rate_limits.c.count - 1, 0))
                )

    def bucket_hash(self, limit: Limit, value: str) -> bytes:
        return hmac_digest(
            self._keys.rate_limit, limit.action, limit.scope, str(limit.window_seconds), value
        )

    async def _reserve_one(self, bucket: Bucket) -> tuple[Slot, bool, int]:
        window = bucket.limit.window_seconds
        bucket_hash = self.bucket_hash(bucket.limit, bucket.value)
        started = _window_start(window)
        statement = (
            insert(rate_limits)
            .values(bucket_hash=bucket_hash, window_start=started, count=1)
            .on_conflict_do_update(
                index_elements=[rate_limits.c.bucket_hash, rate_limits.c.window_start],
                set_={"count": rate_limits.c.count + 1},
            )
            .returning(
                rate_limits.c.count.label("hits"),
                rate_limits.c.window_start,
                func.ceil(
                    func.extract(
                        "epoch",
                        rate_limits.c.window_start
                        + func.make_interval(0, 0, 0, 0, 0, 0, literal(float(window), Float))
                        - func.clock_timestamp(),
                    )
                ).label("retry_after"),
            )
        )
        # Its own transaction, committed at once: the next attempt sees this slot taken.
        async with system_session(self._factory) as session:
            row = (await session.execute(statement)).one()
        slot = Slot(bucket_hash, row.window_start, bucket.on_success)
        return slot, row.hits <= bucket.limit.max_count, max(int(row.retry_after), 1)

    async def begin(self, buckets: Sequence[Bucket]) -> Attempt:
        """Reserve one slot in each bucket. Refused if any bucket is over its limit; the slots
        stay taken then (a refused attempt counts), as ADR 0003 section 9 reserves first."""
        attempt = Attempt(allowed=True, retry_after=0, _factory=self._factory)
        for bucket in buckets:
            slot, ok, retry_after = await self._reserve_one(bucket)
            attempt.slots.append(slot)
            if not ok:
                attempt.allowed = False
                attempt.retry_after = max(attempt.retry_after, retry_after)
        return attempt

    async def begin_together(self, buckets: Sequence[Bucket]) -> Attempt:
        """``begin`` in one statement and one transaction: a slot in every bucket, refused if
        any is over its limit (the slots stay taken). For the general limit, which runs on every
        signed-in request (``api_buckets``). The rows are written in bucket-hash order, so two
        requests that share a bucket (the client address) lock their rows in the same order."""
        rows: dict[bytes, Bucket] = {}
        for bucket in buckets:
            rows.setdefault(self.bucket_hash(bucket.limit, bucket.value), bucket)
        attempt = Attempt(allowed=True, retry_after=0, _factory=self._factory)
        if not rows:
            return attempt
        ordered = sorted(rows)
        statement = (
            insert(rate_limits)
            .values(
                [
                    {
                        "bucket_hash": bucket_hash,
                        "window_start": _window_start(rows[bucket_hash].limit.window_seconds),
                        "count": 1,
                    }
                    for bucket_hash in ordered
                ]
            )
            .on_conflict_do_update(
                index_elements=[rate_limits.c.bucket_hash, rate_limits.c.window_start],
                set_={"count": rate_limits.c.count + 1},
            )
            .returning(
                rate_limits.c.bucket_hash,
                rate_limits.c.count.label("hits"),
                rate_limits.c.window_start,
                func.clock_timestamp().label("now"),
            )
        )
        async with system_session(self._factory) as session:
            found = (await session.execute(statement)).all()
        for row in found:
            bucket = rows[bytes(row.bucket_hash)]
            attempt.slots.append(Slot(bytes(row.bucket_hash), row.window_start, bucket.on_success))
            if row.hits > bucket.limit.max_count:
                ends = row.window_start.timestamp() + bucket.limit.window_seconds
                retry_after = max(math.ceil(ends - row.now.timestamp()), 1)
                attempt.allowed = False
                attempt.retry_after = max(attempt.retry_after, retry_after)
        return attempt

    def cooldown_hash(self, value: str) -> bytes:
        return hmac_digest(self._keys.rate_limit, COVER_COOLDOWN_ACTION, value)

    async def start_cooldown(self, value: str, retry_after: int | None) -> int:
        """Start (or extend) the cover read's cooldown for ``value`` (a user id) by Spotify's
        ``Retry-After``: at least 60 s, at most an hour, 60 s without one. Inserts one row per
        call (its end instant; a duplicate end is ignored). Returns the seconds."""
        seconds = retry_after if isinstance(retry_after, int) and retry_after > 0 else 0
        seconds = min(max(seconds, COVER_COOLDOWN_MIN_SECONDS), COVER_COOLDOWN_MAX_SECONDS)
        ends = func.clock_timestamp() + func.make_interval(
            0, 0, 0, 0, 0, 0, literal(float(seconds), Float)
        )
        statement = (
            insert(rate_limits)
            .values(bucket_hash=self.cooldown_hash(value), window_start=ends, count=0)
            .on_conflict_do_nothing()
        )
        async with system_session(self._factory) as session:
            await session.execute(statement)
        return seconds

    async def cooling_down(self, value: str) -> bool:
        """True while a cooldown of ``value`` has not ended (its end is still in the future)."""
        statement = select(
            exists().where(
                and_(
                    rate_limits.c.bucket_hash == self.cooldown_hash(value),
                    rate_limits.c.window_start > func.clock_timestamp(),
                )
            )
        )
        async with system_session(self._factory) as session:
            return bool((await session.execute(statement)).scalar_one())


class RateLimited(Exception):
    """A limit tripped: the response is 429 with ``Retry-After``."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


def password_buckets(email_normalized: str, ip: str, *, known_device: bool) -> list[Bucket]:
    """Sign-in and re-auth: per (account, IP), per account (not from a known device), per IP."""
    address = bucket_ip(ip)
    buckets = [Bucket(SIGN_IN_PAIR, f"{email_normalized}|{address}", "delete")]
    if not known_device:
        buckets.append(Bucket(SIGN_IN_ACCOUNT, email_normalized))
    buckets.append(Bucket(SIGN_IN_IP, address))
    return buckets


def mfa_buckets(user_id: str, ip: str) -> list[Bucket]:
    """TOTP and recovery code attempts."""
    address = bucket_ip(ip)
    return [
        Bucket(MFA_PAIR, f"{user_id}|{address}", "delete"),
        Bucket(MFA_ACCOUNT, user_id),
        Bucket(MFA_IP, address),
    ]


def connect_buckets(provider: str, user_id: str, ip: str, email_normalized: str) -> list[Bucket]:
    """A provider connect with email and password (MOWL): per user, per IP, per provider email.
    The values are HMAC-keyed like every bucket; the email is never stored in the clear."""
    return [
        Bucket(CONNECT_USER, f"{provider}|{user_id}"),
        Bucket(CONNECT_IP, bucket_ip(ip)),
        Bucket(CONNECT_PROVIDER_EMAIL, f"{provider}|{email_normalized}"),
    ]


def mowl_qr_buckets(user_id: str, ip: str) -> list[Bucket]:
    """A MOWL QR ticket start (ADR 0024 section 5): per user and per IP."""
    return [Bucket(MOWL_QR_USER, user_id), Bucket(MOWL_QR_IP, bucket_ip(ip))]


def spotify_start_buckets(user_id: str, ip: str) -> list[Bucket]:
    """A Spotify connect start (ADR 0009 section 4): per user and per IP."""
    return [Bucket(SPOTIFY_START_USER, user_id), Bucket(SPOTIFY_START_IP, bucket_ip(ip))]


def admin_buckets(user_id: str) -> list[Bucket]:
    """An admin area request (ADR 0011 section 6): 60 per admin and minute."""
    return [Bucket(ADMIN_USER, user_id)]


def admin_mail_test_buckets(user_id: str) -> list[Bucket]:
    """A test send from the admin Mail page (ADR 0022 section 6): 10 per admin and hour."""
    return [Bucket(ADMIN_MAIL_TEST, user_id)]


def redeem_buckets(user_id: str, ip: str) -> list[Bucket]:
    """A redeem-code attempt (ADR 0011 section 2): per user and per IP."""
    return [Bucket(REDEEM_USER, user_id), Bucket(REDEEM_IP, bucket_ip(ip))]


def export_buckets(user_id: str, ip: str) -> list[Bucket]:
    """A data export (ADR 0014 section 2): per user and per IP."""
    return [Bucket(EXPORT_USER, user_id), Bucket(EXPORT_IP, bucket_ip(ip))]


def mowl_playlists_buckets(user_id: str, ip: str) -> list[Bucket]:
    """A listing of the user's Spotify playlists at MOWL (SR-05): per user and per IP."""
    return [Bucket(MOWL_PLAYLISTS_USER, user_id), Bucket(MOWL_PLAYLISTS_IP, bucket_ip(ip))]


def swedebeat_mixes_buckets(user_id: str, ip: str) -> list[Bucket]:
    """A listing of the user's Swedebeat mixes at MOWL (ADR 0016 section 3): per user and per IP."""
    return [Bucket(SWEDEBEAT_MIXES_USER, user_id), Bucket(SWEDEBEAT_MIXES_IP, bucket_ip(ip))]


def playlist_covers_buckets(user_id: str, ip: str) -> list[Bucket]:
    """The picker's cover read (ADR 0013 section 9 amendment, #537): per user and per IP."""
    return [Bucket(PLAYLIST_COVERS_USER, user_id), Bucket(PLAYLIST_COVERS_IP, bucket_ip(ip))]


def _api_read(method: str, path: str) -> bool:
    return method in API_SAFE_METHODS and any(
        path == prefix or path.startswith(prefix + "/") for prefix in API_READ_PREFIXES
    )


def api_limited(path: str) -> bool:
    """Whether the general limit applies to a signed-in request for ``path`` (#577)."""
    return (path == "/api" or path.startswith("/api/")) and path not in API_EXEMPT_PATHS


def api_buckets(
    settings: "Settings", user_id: str, ip: str, method: str, path: str
) -> list[Bucket]:
    """The general limit's buckets of one signed-in ``/api`` request (#577): per user and minute
    and per user and hour (every request), per user and minute for writes (every method but
    ``GET``, ``HEAD`` and ``OPTIONS``) or for the class and job reads, and per client address and
    minute (signed-in requests of every user)."""
    buckets = [
        Bucket(Limit("api", "account", settings.api_requests_per_minute, 60), user_id),
        Bucket(Limit("api", "account", settings.api_requests_per_hour, 3600), user_id),
    ]
    if method not in API_SAFE_METHODS:
        buckets.append(
            Bucket(Limit("api-write", "account", settings.api_writes_per_minute, 60), user_id)
        )
    elif _api_read(method, path):
        buckets.append(
            Bucket(Limit("api-read", "account", settings.api_reads_per_minute, 60), user_id)
        )
    buckets.append(
        Bucket(Limit("api", "ip", settings.api_ip_requests_per_minute, 60), bucket_ip(ip))
    )
    return buckets
