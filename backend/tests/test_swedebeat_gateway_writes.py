"""The gateway's playlist writes (``swedebeat_gateway.writes``, ADR 0036 sections 6 and 8, SR C7)
against the fake gateway: what each sends, which outcome is a definite refusal (nothing written)
and which is **uncertain** (a 5xx after the work, a connection lost after sending, a timeout), the
``403`` write guard, every page of ``/playlists/shared``, the merge streamed under its own 900 s
deadline, and that no failure chains anything or carries a secret. Everything runs over an httpx
transport (the netguard holds: the production clients refuse before an origin is installed).
"""

import json
import traceback
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from classbeat.egress import OutboundBlocked
from classbeat.egress_gateway import (
    MERGE_SECONDS,
    TOTAL_MAX_SECONDS,
    swedebeat_gateway_async_client,
    swedebeat_gateway_upload_client,
)
from classbeat.swedebeat_gateway.writes import (
    WriteFailure,
    create_playlist,
    list_shared,
    merge_publish,
    publish_playlist,
)
from gatewaycore import FIXTURE_MP3, MERGE_PATH, FakeGateway, reference_of
from gatewayfake import CLIENT_KEY, EMAIL, gateway_client

KEY = SecretStr(CLIENT_KEY)
TITLE, ARTIST = "Levitating", "Dua Lipa"
REFERENCE = reference_of(TITLE, ARTIST)
CREATE, SHARED = "/playlists", "/playlists/shared"


@pytest.fixture
def fake(gateway: FakeGateway) -> FakeGateway:
    gateway.add_track(TITLE, ARTIST, "found")
    return gateway


@pytest.fixture
def bearer(fake: FakeGateway) -> str:
    return fake.issue(EMAIL)


def body(name: str = "Ride") -> bytes:
    track = {"reference": REFERENCE, "title": TITLE, "artist": ARTIST, "crossfade_sec": 0}
    payload = {"name": name, "category_id": 27, "crossfade_sec": 0, "tracks": [track]}
    return json.dumps(payload).encode()


def secret_free(failure: BaseException, bearer: str) -> None:
    shown = "".join(traceback.format_exception(failure))
    assert failure.__cause__ is None
    assert failure.__context__ is None
    assert CLIENT_KEY not in shown
    assert bearer not in shown


async def create(fake: FakeGateway, bearer: str) -> str:
    async with gateway_client(fake) as client:
        return await create_playlist(client, KEY, bearer, body())


async def test_a_create_sends_the_body_and_answers_the_new_id(
    fake: FakeGateway, bearer: str
) -> None:
    playlist_id = await create(fake, bearer)
    assert set(fake.playlists) == {playlist_id}
    (sent,) = fake.requests
    assert (sent.method, sent.path, sent.body) == ("POST", CREATE, json.loads(body()))
    assert sent.headers["x-api-key"] == CLIENT_KEY
    assert sent.headers["authorization"] == f"Bearer {bearer}"
    assert sent.headers["content-type"] == "application/json"
    assert sent.headers["accept"] == "application/json"
    assert not {"cookie", "x-forwarded-for"} & set(sent.headers)


async def test_a_publish_sends_the_literal_true_and_needs_the_echo(
    fake: FakeGateway, bearer: str
) -> None:
    playlist_id = await create(fake, bearer)
    async with gateway_client(fake) as client:
        await publish_playlist(client, KEY, bearer, playlist_id)
    assert fake.playlists[playlist_id]["public"] is True
    assert fake.requests[-1].body == {"confirm_irreversible": True}
    async with gateway_client(fake) as client:
        with pytest.raises(ValueError, match="playlist id"):
            await publish_playlist(client, KEY, bearer, "../shared")
    assert len(fake.requests) == 2  # nothing sent for a bad id


@pytest.mark.parametrize(
    ("kind", "status", "category", "written"),
    [
        ("refuse", 403, "writes_off", False),  # never a playlist: the write guard said no
        ("refuse", 422, "invalid_input", False),
        ("refuse", 429, "rate_limited", False),
        ("refuse", 502, "uncertain", False),  # a 5xx is never taken as a refusal
        ("status", 502, "uncertain", True),  # after the work
        ("status", 500, "uncertain", True),
        ("drop", 0, "uncertain", True),  # the connection cut after the work
        ("connect_error", 0, "upstream_unavailable", False),  # it never left
    ],
)
async def test_each_outcome_of_a_create(
    fake: FakeGateway, bearer: str, kind: str, status: int, category: str, written: bool
) -> None:
    fake.inject(kind, CREATE, status=status)
    with pytest.raises(WriteFailure) as failed:
        await create(fake, bearer)
    assert failed.value.category == category
    assert failed.value.sent is (category == "uncertain")
    assert bool(fake.playlists) is written
    secret_free(failed.value, bearer)


async def test_the_write_guard_is_writes_off_for_every_write(
    fake: FakeGateway, bearer: str, tmp_path: Path
) -> None:
    playlist_id = await create(fake, bearer)
    fake.writes_enabled = False
    mp3 = tmp_path / "mix.mp3"
    mp3.write_bytes(FIXTURE_MP3)
    async with gateway_client(fake) as client:
        for write in (
            create_playlist(client, KEY, bearer, body()),
            publish_playlist(client, KEY, bearer, playlist_id),
        ):
            with pytest.raises(WriteFailure) as failed:
                await write
            assert (failed.value.category, failed.value.sent) == ("writes_off", False)
    async with gateway_client(fake, upload=True) as client:
        with pytest.raises(WriteFailure) as failed:
            await merge_publish(client, KEY, bearer, "Ride · cb-abc123", [{"title": "Å"}], mp3)
    assert (failed.value.category, failed.value.sent) == ("writes_off", False)
    assert (fake.mixes, fake.playlists[playlist_id]["public"]) == ([], False)


async def test_a_timeout_after_sending_is_uncertain(fake: FakeGateway, bearer: str) -> None:
    fake.inject("slow", CREATE, seconds=2.0)
    async with gateway_client(fake, total_seconds=1) as client:
        with pytest.raises(WriteFailure) as failed:
            await create_playlist(client, KEY, bearer, body())
    assert (failed.value.category, failed.value.sent) == ("uncertain", True)


async def test_an_unreadable_success_is_uncertain(fake: FakeGateway, bearer: str) -> None:
    fake.inject("body", CREATE, body=b'{"playlist_id": "../x"}')
    with pytest.raises(WriteFailure) as failed:
        await create(fake, bearer)
    assert (failed.value.category, failed.value.sent) == ("uncertain", True)


async def test_the_shared_listing_reads_every_page_until_an_empty_one(
    fake: FakeGateway, bearer: str
) -> None:
    fake.shared_page_size = 2
    made = []
    for _ in range(5):
        playlist_id = await create(fake, bearer)
        fake.playlists[playlist_id]["public"] = True
        made.append(playlist_id)
    fake.playlists["someone-else"] = {"email": "x@y", "name": "x", "tracks": [], "public": True}
    async with gateway_client(fake) as client:
        found = await list_shared(client, KEY, bearer)
    assert found == made  # the caller's own only
    pages = [r.query for r in fake.requests if r.path == SHARED]
    assert pages == ["page=1", "page=2", "page=3", "page=4"]
    assert all(r.method == "GET" for r in fake.requests if r.path == SHARED)


async def test_a_failed_page_or_fifty_full_pages_settle_nothing(
    fake: FakeGateway, bearer: str
) -> None:
    fake.inject("status", SHARED, status=503)
    async with gateway_client(fake) as client:
        with pytest.raises(WriteFailure) as failed:
            await list_shared(client, KEY, bearer)
    assert failed.value.sent is False  # a read: nothing written
    fake.shared_page_size = 0  # an empty first page is the end of the listing
    async with gateway_client(fake) as client:
        assert await list_shared(client, KEY, bearer) == []
    full = [{"playlist_id": f"p{n}"} for n in range(2)]
    for _ in range(50):
        fake.inject("body", SHARED, body=json.dumps(full).encode())
    async with gateway_client(fake) as client:
        with pytest.raises(WriteFailure) as failed:
            await list_shared(client, KEY, bearer)
    assert failed.value.category == "invalid_response"  # it may go beyond page 50


async def test_the_merge_streams_the_file_under_its_name_and_x_tracks(
    fake: FakeGateway, bearer: str, tmp_path: Path
) -> None:
    mp3 = tmp_path / "mix.mp3"
    data = FIXTURE_MP3 + bytes(3 << 20)  # over one 1 MB chunk
    mp3.write_bytes(data)
    tracks = [{"title": "Åsa · Ödla", "artist": "Ärla", "bpm": 128.0, "duration": 192.0}]
    name = "Ride · cb-abc123"
    async with gateway_client(fake, upload=True) as client:
        url = await merge_publish(client, KEY, bearer, name, tracks, mp3)
    (mix,) = fake.mixes
    assert (mix["url"], mix["name"], mix["tracks"]) == (url, name, tracks)
    (sent,) = fake.requests
    assert (sent.method, sent.path, sent.size) == ("POST", MERGE_PATH, len(data))
    assert "confirm_irreversible=true" in sent.query
    assert sent.headers["content-type"] == "audio/mpeg"
    assert sent.headers["content-length"] == str(len(data))
    assert url.startswith("https://")


async def test_a_bad_merge_sends_nothing(fake: FakeGateway, bearer: str, tmp_path: Path) -> None:
    mp3 = tmp_path / "mix.mp3"
    mp3.write_bytes(FIXTURE_MP3)
    async with gateway_client(fake, upload=True) as client:
        with pytest.raises(ValueError, match="mix name"):
            await merge_publish(client, KEY, bearer, "", [{"title": "x"}], mp3)
        with pytest.raises(ValueError, match="X-Tracks"):
            await merge_publish(client, KEY, bearer, "Ride", [], mp3)
        with pytest.raises(FileNotFoundError):
            await merge_publish(client, KEY, bearer, "Ride", [{"title": "x"}], tmp_path / "gone")
    assert fake.requests == []


@pytest.mark.parametrize(("kind", "written"), [("status", True), ("drop", True)])
async def test_a_merge_lost_after_the_work_is_uncertain(
    fake: FakeGateway, bearer: str, tmp_path: Path, kind: str, written: bool
) -> None:
    mp3 = tmp_path / "mix.mp3"
    mp3.write_bytes(FIXTURE_MP3)
    fake.inject(kind, MERGE_PATH, status=502)
    async with gateway_client(fake, upload=True) as client:
        with pytest.raises(WriteFailure) as failed:
            await merge_publish(client, KEY, bearer, "Ride", [{"title": "x"}], mp3)
    assert (failed.value.category, failed.value.sent) == ("uncertain", True)
    assert bool(fake.mixes) is written
    secret_free(failed.value, bearer)


def test_the_upload_client_has_its_own_900_second_deadline(fake: FakeGateway) -> None:
    assert MERGE_SECONDS == 900.0
    assert MERGE_SECONDS > TOTAL_MAX_SECONDS
    upload = gateway_client(fake, upload=True, total_seconds=1)
    assert upload.timeout == httpx.Timeout(MERGE_SECONDS)
    other = gateway_client(fake, total_seconds=10_000)
    assert other.timeout == httpx.Timeout(TOTAL_MAX_SECONDS)  # every other call stays clamped


def test_the_production_clients_refuse_without_an_installed_origin() -> None:
    with pytest.raises(OutboundBlocked):
        swedebeat_gateway_upload_client()
    with pytest.raises(OutboundBlocked):
        swedebeat_gateway_async_client(10)
