"""``GET /api/music/playlist-covers`` (ADR 0013 section 9, amendment of 2026-11-03; threat model
T72, T73, T97; #537): the New class picker's covers from the user's own Spotify playlists.

Against the in-process fake Spotify (``spotifycore``); never the network. A cover on a pinned
shape is answered with its playlist id; other hosts, shapes and queries are left out; another
user's playlists never appear; the read is read-only (nothing recorded, a 401 never flips), sends
no Spotify request without song picking, a connection, or while a song pick is open or a cooldown
runs; a Spotify 429 starts the cooldown; a refresh the read triggers is never cancelled."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mowl import spotify_web
from sqlalchemy import text
from sqlalchemy.engine import Connection

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
