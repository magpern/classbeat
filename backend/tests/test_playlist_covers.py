"""``GET /api/music/playlist-covers`` (ADR 0013 section 9, amendment of 2026-11-03; threat model
T72, T73, T97; #537): the New class picker's covers from the user's own Spotify playlists.

Against the in-process fake Spotify (``spotifycore``); never the network. A cover on a pinned
shape is answered with its playlist id; other hosts, shapes and queries are left out; another
user's playlists never appear; the read is read-only (nothing recorded, a 401 never flips), sends
no Spotify request without song picking, a connection, or while a song pick is open or a cooldown
runs; a Spotify 429 starts the cooldown; a refresh the read triggers is never cancelled."""

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mowl import spotify_web
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat import egress
from classbeat.auth import ratelimit
from classbeat.spotify import access
from classbeat.spotify.http import SpotifyHttp
from jobtools import new_job
from mfatools import Actor
from spotifycore import ACCOUNTS_HOST, API_HOST, playlist_images
from spotifyfake import CLIENT_A, CLIENT_B, USER_A, USER_B
from spotifytools import Spot

URL = "/api/music/playlist-covers"
LISTING = "/v1/me/playlists"
TOKEN = "/api/token"
FIXED = {
    "mosaic": "MosaicList000000000001",
    "album": "AlbumList0000000000001",
    "uploaded": "UploadedList0000000001",
    "uploaded_fa": "UploadedFaList00000001",
    "foreign": "ForeignList00000000001",
    "query": "QueryList0000000000001",
    "none": "NoImageList00000000001",
}
PINNED = ("mosaic", "album", "uploaded", "uploaded_fa")


async def rider(
    spot: Spot,
    admin: Connection,
    *,
    client_id: str = CLIENT_A,
    user: str = USER_A,
    entitled: bool = True,
    connected: bool = True,
    expires_in: float = 3000,
) -> tuple[Actor, Any]:
    actor = await spot.actor(f"covers-{uuid.uuid4().hex[:10]}@example.com")
    ctx = spot.ctx(actor)
    if entitled:  # the default song_picking flag is ``granted`` (ADR 0011 section 7)
        admin.execute(
            text(
                "INSERT INTO entitlements (owner_id, features, source) "
                "VALUES (:o, ARRAY['song_picking'], 'grant')"
            ),
            {"o": spot.user_id(actor)},
        )
    if connected:
        await spot.seed(ctx, client_id, user, expires_in=expires_in)
    return actor, ctx


def playlists(spot: Spot, owner: str = USER_A, prefix: str = "") -> dict[str, str]:
    """One playlist of ``owner`` per image kind; the ids by kind."""
    made: dict[str, str] = {}
    for kind, playlist_id in FIXED.items():
        pid = (prefix + playlist_id)[:22]
        spot.fake.add_playlist(
            owner, f"{kind} list", playlist_id=pid, images=playlist_images(kind, pid)
        )
        made[kind] = pid
    return made


def listing_requests(spot: Spot) -> int:
    return len(spot.fake.requests(API_HOST, LISTING))


async def row(spot: Spot, ctx: Any) -> Any:
    rows = await spot.owned(
        ctx,
        "SELECT status, last_error, last_used_at, secret, wrapped_dek, generation "
        "FROM connections WHERE provider = 'spotify'",
    )
    return rows[0] if rows else None


@pytest.fixture
def admin(spotify_admin: Connection) -> Connection:
    return spotify_admin


async def test_covers_on_the_pinned_shapes_are_answered_by_playlist_id(
    spot: Spot, admin: Connection
) -> None:
    actor, ctx = await rider(spot, admin)
    made = playlists(spot)
    before = await row(spot, ctx)
    answer = await actor.get(URL)
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "no-store"
    covers = {c["spotify_playlist_id"]: c["cover"] for c in answer.json()["covers"]}
    assert set(covers) == {made[kind] for kind in PINNED}  # foreign, query, none: left out
    assert covers[made["mosaic"]].startswith("https://mosaic.scdn.co/300/")  # 60 px is too small
    assert covers[made["album"]].startswith("https://i.scdn.co/image/")
    assert covers[made["uploaded"]].startswith("https://image-cdn-ak.spotifycdn.com/image/")
    assert covers[made["uploaded_fa"]].startswith("https://image-cdn-fa.spotifycdn.com/image/")
    assert all(set(c) == {"spotify_playlist_id", "cover"} for c in answer.json()["covers"])
    # One listing request, no refresh, and nothing recorded on the connection.
    assert [(s.host, s.path) for s in spot.fake.seen if s.host == API_HOST] == [(API_HOST, LISTING)]
    assert spot.fake.requests(ACCOUNTS_HOST, TOKEN) == []
    assert await row(spot, ctx) == before


async def test_another_users_playlists_never_appear(spot: Spot, admin: Connection) -> None:
    mine, _ = await rider(spot, admin, client_id=CLIENT_A, user=USER_A)
    other, _ = await rider(spot, admin, client_id=CLIENT_B, user=USER_B)
    a = playlists(spot, USER_A)
    b = playlists(spot, USER_B, prefix="OtherUserB")
    got_a = {c["spotify_playlist_id"] for c in (await mine.get(URL)).json()["covers"]}
    got_b = {c["spotify_playlist_id"] for c in (await other.get(URL)).json()["covers"]}
    assert got_a == {a[k] for k in PINNED}
    assert got_b == {b[k] for k in PINNED}
    assert not got_a & set(b.values())
    assert not got_b & set(a.values())


@pytest.mark.parametrize("setup", ["not_entitled", "not_connected"])
async def test_without_song_picking_or_a_connection_no_spotify_request_is_sent(
    spot: Spot, admin: Connection, setup: str
) -> None:
    actor, _ = await rider(
        spot, admin, entitled=setup != "not_entitled", connected=setup != "not_connected"
    )
    playlists(spot)
    answer = await actor.get(URL)
    assert (answer.status_code, answer.json()) == (200, {"covers": []})
    assert spot.fake.seen == []


def plant_job(admin: Connection, owner: uuid.UUID, kind: str, state: str) -> uuid.UUID:
    """A job of ``kind`` in ``state`` with the columns that state needs."""
    extra: dict[str, Any] = {}
    if state == "running":
        extra = {
            "attempt": 1,
            "leased_by": "other-worker",
            "lease_expires_at": datetime.now(UTC) + timedelta(minutes=5),
        }
    elif state == "waiting_for_approval":
        extra = {"approval_hash": b"\x01" * 32, "expires_at": datetime.now(UTC) + timedelta(days=1)}
    return new_job(admin, owner, kind=kind, state=state, **extra)


async def test_with_song_picking_off_no_spotify_request_is_sent(
    spot: Spot, admin: Connection
) -> None:
    """``CLASSBEAT_SONG_PICKING`` off is the kill switch: an entitled user with a connected Spotify
    row gets no covers and nothing is sent (the route's own check, ``design/routes.py``)."""
    actor, ctx = await rider(spot, admin)  # entitled and connected
    assert await row(spot, ctx) is not None
    playlists(spot)
    state = spot.env.app.state
    settings = state.settings
    state.settings = settings.model_copy(update={"song_picking": False})
    try:
        answer = await actor.get(URL)
    finally:
        state.settings = settings
    assert (answer.status_code, answer.json()) == (200, {"covers": []})
    assert spot.fake.seen == []
    # The same user with the setting back on is read: the check above is what stopped it.
    assert len((await actor.get(URL)).json()["covers"]) == len(PINNED)


def deadline_left() -> float | None:
    """The seconds left on this thread's ``egress.call_deadline``, or None without one."""
    at = egress.call_deadline_at()
    return None if at is None else at - time.monotonic()


@pytest.mark.parametrize("expires_in", [3000, 10])
async def test_the_listing_requests_run_under_an_8_second_deadline_and_the_refresh_under_none(
    spot: Spot, admin: Connection, monkeypatch: pytest.MonkeyPatch, expires_in: float
) -> None:
    """ADR 0013 section 9, "What the 8 s bounds": ``my_playlists`` runs inside
    ``egress.call_deadline(8)`` in its thread; a refresh (``expires_in=10``) runs before it, on
    the event loop, under no call deadline (it keeps its own lock wait and timeout)."""
    actor, _ = await rider(spot, admin, expires_in=expires_in)
    playlists(spot)
    seen: list[float | None] = []
    listing = spotify_web.my_playlists

    def recorded(*args: Any, **kwargs: Any) -> Any:
        seen.append(deadline_left())
        return listing(*args, **kwargs)

    monkeypatch.setattr(spotify_web, "my_playlists", recorded)
    held = HeldRefresh(spot.http)
    held.go_on.set()
    refresh_deadlines: list[float | None] = []
    inner_refresh = held.refresh

    async def refresh(*args: Any, **kwargs: Any) -> Any:
        refresh_deadlines.append(deadline_left())
        return await inner_refresh(*args, **kwargs)

    monkeypatch.setattr(held, "refresh", refresh)
    state = spot.env.app.state
    state.spotify_http = held
    try:
        assert len((await actor.get(URL)).json()["covers"]) == len(PINNED)
    finally:
        state.spotify_http = spot.http
    assert len(seen) == 1
    left = seen[0]
    assert left is not None, "the listing ran with no call deadline"
    assert 0 < left <= 8.0
    assert left > 6.0  # it was set just before the listing, not inherited from elsewhere
    assert refresh_deadlines == ([None] if expires_in == 10 else [])


@pytest.mark.parametrize("kind", ["design_with_songs", "revise_songs"])
@pytest.mark.parametrize("state", ["queued", "running"])
async def test_a_queued_or_running_song_pick_holds_the_read(
    spot: Spot, admin: Connection, kind: str, state: str
) -> None:
    actor, _ = await rider(spot, admin)
    playlists(spot)
    plant_job(admin, spot.user_id(actor), kind, state)
    assert (await actor.get(URL)).json() == {"covers": []}
    assert listing_requests(spot) == 0


@pytest.mark.parametrize(
    ("kind", "state"),
    [
        ("design_with_songs", "waiting_for_approval"),  # not counted (ADR 0013 section 9)
        ("design_draft", "running"),  # not a Spotify-calling kind
        ("design_with_songs", "completed"),
    ],
)
async def test_other_jobs_do_not_hold_the_read(
    spot: Spot, admin: Connection, kind: str, state: str
) -> None:
    actor, _ = await rider(spot, admin)
    playlists(spot)
    if state == "completed":
        new_job(admin, spot.user_id(actor), kind=kind, state=state, finished_at=datetime.now(UTC))
    else:
        plant_job(admin, spot.user_id(actor), kind, state)
    assert len((await actor.get(URL)).json()["covers"]) == len(PINNED)
    assert listing_requests(spot) == 1


async def test_another_users_open_pick_does_not_hold_mine(spot: Spot, admin: Connection) -> None:
    mine, _ = await rider(spot, admin, client_id=CLIENT_A, user=USER_A)
    other, _ = await rider(spot, admin, client_id=CLIENT_B, user=USER_B)
    playlists(spot)
    plant_job(admin, spot.user_id(other), "design_with_songs", "queued")
    assert len((await mine.get(URL)).json()["covers"]) == len(PINNED)


@pytest.mark.parametrize(
    ("retry_after", "expected"), [(5, 60), (None, 60), (600, 600), (99999, 3600)]
)
async def test_a_spotify_429_starts_the_cooldown_and_touches_nothing_else(
    spot: Spot, admin: Connection, retry_after: int | None, expected: int
) -> None:
    actor, ctx = await rider(spot, admin)
    playlists(spot)
    before = await row(spot, ctx)
    options: dict[str, Any] = {"path": LISTING}
    if retry_after is not None:
        options["retry_after"] = retry_after
    spot.fake.inject("rate_limited", **options)
    assert (await actor.get(URL)).json() == {"covers": []}
    assert listing_requests(spot) == 1
    # The cooldown: no Spotify request until it ends, then the read is back.
    limiter = spot.env.app.state.limiter
    user = str(spot.user_id(actor))
    assert await limiter.cooling_down(user)
    remaining = admin.execute(
        text(
            "SELECT extract(epoch FROM window_start - clock_timestamp()) FROM rate_limits "
            "WHERE bucket_hash = :h"
        ),
        {"h": limiter.cooldown_hash(user)},
    ).scalar_one()
    assert expected - 5 < float(remaining) <= expected
    assert (await actor.get(URL)).json() == {"covers": []}
    assert listing_requests(spot) == 1
    assert await row(spot, ctx) == before  # no last_error, no status change
    admin.execute(
        text("UPDATE rate_limits SET window_start = clock_timestamp() WHERE bucket_hash = :h"),
        {"h": limiter.cooldown_hash(user)},
    )
    assert not await limiter.cooling_down(user)
    assert len((await actor.get(URL)).json()["covers"]) == len(PINNED)


async def test_the_cooldown_is_per_user(spot: Spot, admin: Connection) -> None:
    limiter = spot.env.app.state.limiter
    assert await limiter.start_cooldown("user-a", 120) == 120
    assert await limiter.cooling_down("user-a")
    assert not await limiter.cooling_down("user-b")
    assert limiter.cooldown_hash("user-a") != limiter.cooldown_hash("user-b")


async def test_a_401_is_not_repeated_never_refreshes_and_never_flips(
    spot: Spot, admin: Connection
) -> None:
    actor, ctx = await rider(spot, admin)
    playlists(spot)
    before = await row(spot, ctx)
    spot.fake.inject("status", path=LISTING, status=401, times=5)
    assert (await actor.get(URL)).json() == {"covers": []}
    assert listing_requests(spot) == 1
    assert spot.fake.requests(ACCOUNTS_HOST, TOKEN) == []
    after = await row(spot, ctx)
    assert after == before
    assert after.status == "connected"


@pytest.mark.parametrize("status", [403, 404, 500, 503])
async def test_any_other_failure_is_no_covers_and_records_nothing(
    spot: Spot, admin: Connection, status: int
) -> None:
    actor, ctx = await rider(spot, admin)
    playlists(spot)
    before = await row(spot, ctx)
    spot.fake.inject("status", path=LISTING, status=status, times=5)
    answer = await actor.get(URL)
    assert (answer.status_code, answer.json()) == (200, {"covers": []})
    assert await row(spot, ctx) == before


async def test_more_than_500_playlists_is_no_covers_not_a_truncation(
    spot: Spot, admin: Connection
) -> None:
    actor, _ = await rider(spot, admin)
    for n in range(501):
        spot.fake.add_playlist(USER_A, f"L{n}", images=playlist_images("album", str(n)))
    assert (await actor.get(URL)).json() == {"covers": []}
    assert listing_requests(spot) == 1  # the first page's total is over the cap


async def test_an_expiring_token_is_refreshed_first_and_the_rotation_is_stored(
    spot: Spot, admin: Connection
) -> None:
    actor, ctx = await rider(spot, admin, expires_in=10)
    playlists(spot)
    before = await row(spot, ctx)
    assert len((await actor.get(URL)).json()["covers"]) == len(PINNED)
    assert len(spot.fake.requests(ACCOUNTS_HOST, TOKEN)) == 1
    after = await row(spot, ctx)
    assert after.secret != before.secret  # the rotated tokens are stored
    assert (after.status, after.last_error, after.last_used_at) == (
        before.status,
        before.last_error,
        before.last_used_at,
    )


@pytest.mark.usefixtures("frozen_rate_windows")  # #588
async def test_the_own_rate_limit_comes_before_any_call(spot: Spot, admin: Connection) -> None:
    actor, _ = await rider(spot, admin)
    playlists(spot)
    limit = ratelimit.PLAYLIST_COVERS_USER.max_count
    assert (limit, ratelimit.PLAYLIST_COVERS_IP.max_count) == (10, 30)
    for _ in range(limit):
        assert (await actor.get(URL)).status_code == 200
    refused = await actor.get(URL)
    assert refused.status_code == 429
    assert refused.json() == {"detail": {"reason": "rate_limited"}}
    assert 0 < int(refused.headers["Retry-After"]) <= 600
    assert listing_requests(spot) == limit


async def test_signed_out_is_401(spot: Spot) -> None:
    assert (await spot.env.client.get(URL)).status_code == 401
    assert spot.fake.seen == []


# --- the read-only mode of spotify_call -----------------------------------------------------


class HeldRefresh(SpotifyHttp):
    """The fake's token client, whose refresh waits until the test lets it go on."""

    def __init__(self, inner: SpotifyHttp) -> None:
        self.inner = inner
        self.started = asyncio.Event()
        self.go_on = asyncio.Event()
        self.finished = False

    async def refresh(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        self.started.set()
        await self.go_on.wait()
        try:
            return await self.inner.refresh(*args, **kwargs)
        finally:
            self.finished = True


async def test_a_cancelled_read_never_cancels_its_refresh(spot: Spot, admin: Connection) -> None:
    _, ctx = await rider(spot, admin, expires_in=10)
    before = await row(spot, ctx)
    held = HeldRefresh(spot.http)
    call = asyncio.ensure_future(
        access.spotify_call(
            spot.env.factory, spot.ring, ctx, spotify_web.my_playlists, http=held, read_only=True
        )
    )
    await asyncio.wait_for(held.started.wait(), 5)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert len(access._SHIELDED) == 1  # pyright: ignore[reportPrivateUsage]
    held.go_on.set()
    await asyncio.gather(*access._SHIELDED)  # pyright: ignore[reportPrivateUsage]
    assert held.finished
    assert set() == access._SHIELDED  # pyright: ignore[reportPrivateUsage]
    assert len(spot.fake.requests(ACCOUNTS_HOST, TOKEN)) == 1
    assert (await row(spot, ctx)).secret != before.secret  # the rotation was stored
    assert listing_requests(spot) == 0  # only the refresh ran on: no read after the cancel


async def test_the_read_only_mode_never_writes() -> None:
    with pytest.raises(ValueError, match="never writes"):
        await access._call(  # pyright: ignore[reportPrivateUsage]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            lambda _creds: None,
            http=None,
            write=True,
            margin_seconds=120,
            expect=None,
            single_request=False,
            read_only=True,
        )


def test_a_cover_can_never_ride_in_a_design_jobs_music_input() -> None:
    """Nothing new reaches the model: the job's music choice is the playlist id alone and refuses
    any other field (ADR 0013 section 9 amendment: never to or from a model)."""
    from pydantic import ValidationError

    from classbeat.design.kind import SpotifyMusicChoice

    playlist = "MosaicList000000000001"
    choice = SpotifyMusicChoice.model_validate(
        {"source": "mowl_spotify_playlist", "spotify_playlist_id": playlist}
    )
    assert "cover" not in choice.model_dump()
    assert "cover" not in json.dumps(SpotifyMusicChoice.model_json_schema())
    with pytest.raises(ValidationError):
        SpotifyMusicChoice.model_validate(
            {
                "source": "mowl_spotify_playlist",
                "spotify_playlist_id": playlist,
                "cover": f"https://i.scdn.co/image/{'a' * 40}",
            }
        )


# --- hardening (#470): a gate that cannot answer, and the re-check before the listing ---------

CANARY = "covers-db-canary-7f3a"


def db_error() -> Exception:
    from sqlalchemy.exc import OperationalError

    return OperationalError(f"SELECT {CANARY}", {"p": CANARY}, Exception(CANARY))


@pytest.mark.parametrize("where", ["cooling_down", "admitted_first", "admitted_again"])
async def test_a_database_error_in_a_gate_is_no_covers_and_sends_nothing(
    spot: Spot,
    admin: Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    where: str,
) -> None:
    """ADR 0013 section 9: every failure is ``{covers: []}``. A gate that cannot answer gives no
    covers (it never counts as admitted), no listing is sent, and the log names the category
    only, never the error's text."""
    from classbeat.jobs import service as jobs_service

    actor, _ = await rider(spot, admin)
    playlists(spot)
    limiter = spot.env.app.state.limiter
    if where == "cooling_down":

        async def broken_cooldown(_value: str) -> bool:
            raise db_error()

        monkeypatch.setattr(limiter, "cooling_down", broken_cooldown)
    else:
        real = jobs_service.cover_read_admitted
        calls: list[int] = []

        async def broken_admitted(*args: Any, **kwargs: Any) -> bool:
            calls.append(1)
            if where == "admitted_first" or len(calls) == 2:
                raise db_error()
            return await real(*args, **kwargs)

        monkeypatch.setattr(jobs_service, "cover_read_admitted", broken_admitted)
    caplog.set_level("INFO")
    answer = await actor.get(URL)
    assert (answer.status_code, answer.json()) == (200, {"covers": []})
    assert listing_requests(spot) == 0
    assert "playlist covers unavailable: database_error" in caplog.text
    assert CANARY not in caplog.text


async def test_a_database_error_storing_the_cooldown_after_a_429_is_no_covers(
    spot: Spot, admin: Connection, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    actor, _ = await rider(spot, admin)
    playlists(spot)
    limiter = spot.env.app.state.limiter

    async def broken_start(_value: str, _retry_after: int | None) -> int:
        raise db_error()

    monkeypatch.setattr(limiter, "start_cooldown", broken_start)
    spot.fake.inject("rate_limited", path=LISTING, retry_after=120)
    caplog.set_level("INFO")
    answer = await actor.get(URL)
    assert (answer.status_code, answer.json()) == (200, {"covers": []})
    assert listing_requests(spot) == 1
    assert "playlist covers unavailable: database_error" in caplog.text
    assert CANARY not in caplog.text


@pytest.mark.parametrize("opened", ["song_pick", "cooldown"])
async def test_a_pick_or_cooldown_that_starts_during_the_refresh_holds_the_listing(
    spot: Spot, admin: Connection, opened: str
) -> None:
    """The gates are checked again after the refresh, right before the listing (#470): a song
    pick queued (or a cooldown started) while the refresh runs sends no listing request. The
    refresh itself still completes and stores its rotation."""
    actor, ctx = await rider(spot, admin, expires_in=10)
    playlists(spot)
    before = await row(spot, ctx)
    held = HeldRefresh(spot.http)
    state = spot.env.app.state
    state.spotify_http = held
    try:
        pending = asyncio.ensure_future(actor.get(URL))
        await asyncio.wait_for(held.started.wait(), 5)
        if opened == "song_pick":
            plant_job(admin, spot.user_id(actor), "design_with_songs", "queued")
        else:
            await state.limiter.start_cooldown(str(spot.user_id(actor)), 120)
        held.go_on.set()
        answer = await asyncio.wait_for(pending, 10)
    finally:
        state.spotify_http = spot.http
    assert (answer.status_code, answer.json()) == (200, {"covers": []})
    assert listing_requests(spot) == 0
    assert held.finished
    assert len(spot.fake.requests(ACCOUNTS_HOST, TOKEN)) == 1
    assert (await row(spot, ctx)).secret != before.secret


async def test_before_read_is_the_read_only_modes_only() -> None:
    """A real user context and a factory that fails if used: the refusal is a ``ValueError``
    raised before anything is opened (#608), not whatever a missing argument happens to raise."""
    from ctx import make_context

    async def hook() -> None:
        return None

    def untouchable(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("no database session may be opened")

    with pytest.raises(ValueError, match="read-only"):
        await access._call(  # pyright: ignore[reportPrivateUsage]
            untouchable,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            make_context(),
            lambda _creds: None,
            http=None,
            write=False,
            margin_seconds=120,
            expect=None,
            single_request=False,
            before_read=hook,
        )


async def test_a_database_error_opening_the_connection_is_no_covers(
    spot: Spot, admin: Connection, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """ADR 0013 section 9 (#470, #610): the gates passed, then opening the Spotify connection
    fails in the database. No covers, no listing, the category only in the log."""
    actor, _ = await rider(spot, admin)
    playlists(spot)

    async def broken_open(*_args: Any, **_kwargs: Any) -> Any:
        raise db_error()

    monkeypatch.setattr(access, "open_connection", broken_open)
    caplog.set_level("INFO")
    answer = await actor.get(URL)
    assert (answer.status_code, answer.json()) == (200, {"covers": []})
    assert listing_requests(spot) == 0
    assert "playlist covers unavailable: database_error" in caplog.text
    assert CANARY not in caplog.text


async def test_a_database_error_in_the_rate_limit_check_is_the_apps_500(
    spot: Spot, admin: Connection, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """ADR 0013 section 9 (#610): the limiter runs before the read and is not one of its
    gates. A limiter that cannot count answers the app's ordinary 500 (no error text), never
    ``{covers: []}`` and never an unlimited read."""
    actor, _ = await rider(spot, admin)
    playlists(spot)

    async def broken_begin(_buckets: Any) -> Any:
        raise db_error()

    monkeypatch.setattr(spot.env.app.state.limiter, "begin", broken_begin)
    answer = await actor.get(URL)
    assert answer.status_code == 500
    assert CANARY not in answer.text
    assert CANARY not in caplog.text
    assert listing_requests(spot) == 0
