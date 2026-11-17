"""A Spotify answer compressed although classbeat asked for identity (#1149): the token
endpoint and ``GET /me`` still decode, the ``BODY_MAX`` cap counts decoded bytes and a
decompression bomb is refused without being read whole. The fake Spotify only, never the
network."""

import gzip
import random
import zlib
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from mowl import spotify_web

from classbeat.connections import ConnectionFailure
from classbeat.egress import spotify_async_client
from classbeat.spotify import spotify_call
from classbeat.spotify.answer_body import RAW_SLACK, RAW_SLACK_DIVISOR, CappedBody
from classbeat.spotify.http import AUTHORIZE_URL, SpotifyHttp
from spotifycore import ACCOUNTS_HOST, API_HOST, SCOPES, FakeSpotify, challenge_of
from spotifyfake import CALLBACK, CLIENT_A, USER_A, spotify_client
from spotifytools import Spot

VERIFIER = "v" * 64


def _http(fake: FakeSpotify) -> SpotifyHttp:
    return SpotifyHttp(lambda total: spotify_client(fake, total_seconds=total))


def _code(fake: FakeSpotify) -> str:
    """The code of user A's consent to app A, as the browser brings it back."""
    query = {
        "client_id": CLIENT_A,
        "response_type": "code",
        "redirect_uri": CALLBACK,
        "scope": SCOPES,
        "state": "s" * 32,
        "code_challenge_method": "S256",
        "code_challenge": challenge_of(VERIFIER),
    }
    answer = fake.handle("GET", f"{AUTHORIZE_URL}?{urlencode(query)}", {}, None)
    assert answer.status == 302
    return parse_qs(urlsplit(answer.headers["Location"]).query)["code"][0]


async def test_exchange_me_and_refresh_decode_a_gzip_answer(spotify: FakeSpotify) -> None:
    spotify.gzip = True
    http = _http(spotify)
    granted = await http.exchange_code(
        client_id=CLIENT_A, code=_code(spotify), redirect_uri=CALLBACK, verifier=VERIFIER
    )
    assert granted.refresh_token is not None
    assert await http.me(granted.access_token) == USER_A
    refreshed = await http.refresh(CLIENT_A, granted.refresh_token)
    assert await http.me(refreshed.access_token) == USER_A
    assert [s.status for s in spotify.seen[1:]] == [200, 200, 200, 200]


async def test_every_request_asks_for_identity(spotify: FakeSpotify) -> None:
    http = _http(spotify)
    access, refresh = spotify.grant(CLIENT_A, USER_A)
    await http.me(access)
    await http.refresh(CLIENT_A, refresh)
    sent = spotify.requests(API_HOST, "/v1/me") + spotify.requests(ACCOUNTS_HOST, "/api/token")
    assert [s.accept_encoding for s in sent] == ["identity", "identity"]


class _Answer(httpx.AsyncBaseTransport):
    """Every request answered 200 with ``body`` and ``encoding``, in ``chunk``-byte pieces;
    ``pulled`` counts the pieces read."""

    def __init__(self, body: bytes, encoding: str | None, chunk: int = 1024) -> None:
        self.body, self.encoding, self.chunk = body, encoding, chunk
        self.pulled = 0

    async def _pieces(self) -> AsyncIterator[bytes]:
        for at in range(0, len(self.body), self.chunk):
            self.pulled += 1
            yield self.body[at : at + self.chunk]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if self.encoding is not None:
            headers["Content-Encoding"] = self.encoding
        stream = _Stream(self._pieces())
        return httpx.Response(200, headers=headers, stream=stream, request=request)


class _Stream(httpx.AsyncByteStream):
    def __init__(self, pieces: AsyncIterator[bytes]) -> None:
        self._pieces = pieces

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for piece in self._pieces:
            yield piece


def _over(transport: _Answer) -> SpotifyHttp:
    return SpotifyHttp(lambda total: spotify_async_client(total_seconds=total, inner=transport))


async def _refused(http: SpotifyHttp) -> list[str]:
    categories: list[str] = []
    for call in (lambda: http.me("a" * 40), lambda: http.refresh(CLIENT_A, "r" * 40)):
        with pytest.raises(ConnectionFailure) as failure:
            await call()
        categories.append(failure.value.category)
    return categories


async def test_a_gzip_bomb_is_invalid_and_not_read_whole() -> None:
    # A JSON string of 50 MB of spaces: about 50 kB compressed, past BODY_MAX at once decoded.
    bomb = gzip.compress(b'{"id": "' + b" " * (50 * 1024 * 1024) + b'"}')
    transport = _Answer(bomb, "gzip")
    assert await _refused(_over(transport)) == ["invalid_response", "invalid_response"]
    pieces = -(-len(bomb) // transport.chunk)
    assert transport.pulled < pieces / 4  # each call stopped at the cap


@pytest.mark.parametrize(
    ("body", "encoding"),
    [
        (b'{"id": "rider-one"}', "br"),  # an encoding we do not decode
        (gzip.compress(gzip.compress(b'{"id": "rider-one"}')), "gzip, gzip"),  # stacked
        (b'{"id": "rider-one"}', "gzip"),  # not gzip at all
        (gzip.compress(b'{"id": "rider-one"}')[:-8], "gzip"),  # cut before its end
    ],
)
async def test_an_answer_it_cannot_decode_is_invalid(body: bytes, encoding: str) -> None:
    assert await _refused(_over(_Answer(body, encoding))) == ["invalid_response"] * 2


async def test_a_deflate_answer_decodes() -> None:
    http = _over(_Answer(zlib.compress(b'{"id": "rider-one"}'), "deflate"))
    assert await http.me("a" * 40) == "rider-one"


def _decode(raw: bytes, encoding: str | None, cap: int, chunk: int = 7) -> bytes | None:
    capped = CappedBody(encoding, cap)
    for at in range(0, len(raw), chunk):
        if not capped.feed(raw[at : at + chunk]):
            break
    return capped.body()


@pytest.mark.parametrize("encoding", [None, "", "identity", " Identity "])
def test_identity_is_kept_and_cut_at_cap_plus_one(encoding: str | None) -> None:
    assert _decode(b"x" * 10, encoding, 10) == b"x" * 10
    assert _decode(b"x" * 100, encoding, 10) == b"x" * 11


@pytest.mark.parametrize(
    ("encoding", "pack"),
    [("gzip", gzip.compress), ("X-Gzip", gzip.compress), ("deflate", zlib.compress)],
)
def test_compressed_bodies_are_capped_on_decoded_bytes(encoding: str, pack: Any) -> None:
    assert _decode(pack(b"y" * 10), encoding, 10) == b"y" * 10
    assert _decode(pack(b"y" * 100_000), encoding, 10) == b"y" * 11
    assert _decode(pack(b"y" * 100_000), encoding, 10, chunk=1 << 20) == b"y" * 11


@pytest.mark.parametrize(
    ("encoding", "pack"), [("gzip", gzip.compress), ("deflate", zlib.compress)]
)
@pytest.mark.parametrize("tail", [b"\x00", b"\x00" * 64, b'{"x": 1}'])
@pytest.mark.parametrize("chunk", [1, 7, 1 << 20])
def test_data_after_the_end_of_the_stream_is_refused(
    encoding: str, pack: Any, tail: bytes, chunk: int
) -> None:
    assert _decode(pack(b'{"id": "a"}'), encoding, 100, chunk) == b'{"id": "a"}'
    assert _decode(pack(b'{"id": "a"}') + tail, encoding, 100, chunk) is None


def test_raw_bytes_far_past_the_cap_are_refused() -> None:
    # One flushed block per byte: about ten raw bytes per decoded byte, so the raw bound trips
    # long before the decoded cap does.
    pack = zlib.compressobj()
    raw = b"".join(pack.compress(b"a") + pack.flush(zlib.Z_FULL_FLUSH) for _ in range(5000))
    capped = CappedBody("deflate", 10_000)
    fed = [capped.feed(raw[at : at + 500]) for at in range(0, len(raw), 500)]
    assert fed[0] is True
    assert fed[-1] is False
    assert capped.body() is None


@pytest.mark.parametrize(
    ("encoding", "pack"), [("gzip", gzip.compress), ("deflate", zlib.compress)]
)
@pytest.mark.parametrize("chunk", [1, 7, 1 << 20])
def test_an_empty_chunk_after_the_end_of_the_stream_is_not_data(
    encoding: str, pack: Any, chunk: int
) -> None:
    raw = pack(b'{"id": "a"}')
    for tail in ([b""], [b"", b""]):
        capped = CappedBody(encoding, 100)
        fed = [capped.feed(raw[at : at + chunk]) for at in range(0, len(raw), chunk)]
        fed += [capped.feed(empty) for empty in tail]
        assert all(fed)
        assert capped.body() == b'{"id": "a"}'


_CAP = 1 << 16
_RAW_LIMIT = _CAP + _CAP // RAW_SLACK_DIVISOR + RAW_SLACK


def _incompressible() -> bytes:
    return random.Random(7).randbytes(_CAP)  # noqa: S311 - a fixed, reproducible test payload


def _flushed(data: bytes, period: int, mem_level: int) -> bytes:
    """``data`` as a valid deflate stream with a sync flush every ``period`` bytes."""
    pack = zlib.compressobj(6, zlib.DEFLATED, zlib.MAX_WBITS, mem_level)
    out = b""
    for at in range(0, len(data), period):
        out += pack.compress(data[at : at + period]) + pack.flush(zlib.Z_SYNC_FLUSH)
    return out + pack.flush()


def test_an_encoder_with_large_overhead_is_accepted() -> None:
    # Incompressible data of exactly the cap, flushed every 512 bytes with memLevel 1: the
    # overhead (about 3 kB) is past the old fixed slack and inside the one that scales.
    data = _incompressible()
    raw = _flushed(data, 512, 1)
    assert RAW_SLACK < len(raw) - _CAP < _RAW_LIMIT - _CAP  # old bound refused it, new one not
    assert _decode(raw, "deflate", _CAP, chunk=1000) == data


def test_an_encoder_past_the_scaled_raw_bound_is_still_refused() -> None:
    # The same data flushed every 64 bytes: the overhead (about 10 kB) is past the bound.
    data = _incompressible()
    raw = _flushed(data, 64, 1)
    assert len(raw) > _RAW_LIMIT
    capped = CappedBody("deflate", _CAP)
    fed = [capped.feed(raw[at : at + 1000]) for at in range(0, len(raw), 1000)]
    assert fed[-1] is False
    assert capped.body() is None


async def test_a_gzip_answer_with_trailing_bytes_is_invalid() -> None:
    body = gzip.compress(b'{"id": "rider-one"}') + b"\x00" * 16
    assert await _refused(_over(_Answer(body, "gzip"))) == ["invalid_response"] * 2


def test_a_negative_cap_is_refused() -> None:
    with pytest.raises(ValueError, match="cap"):
        CappedBody(None, -1)


async def test_a_connect_works_with_gzip_answers(spot: Spot) -> None:
    spot.fake.gzip = True
    actor = await spot.actor("rider@example.com")
    assert await spot.connect(actor) == {"result": "connected"}
    assert [s.status for s in spot.token_requests()] == [200]
    assert [s.status for s in spot.fake.requests(API_HOST, "/v1/me")] == [200]
    row = await spot.connection(spot.ctx(actor))
    assert (row.status, row.last_error) == ("connected", None)


async def test_a_stored_connections_refresh_works_with_a_gzip_answer(spot: Spot) -> None:
    spot.fake.gzip = True
    ctx = spot.ctx(await spot.actor("rider@example.com"))
    await spot.seed(ctx, expires_in=30)
    assert await spotify_call(spot.env.factory, spot.ring, ctx, spotify_web.me, http=spot.http) == {
        "id": USER_A
    }
    assert [s.status for s in spot.token_requests()] == [200]
    row = await spot.connection(ctx)
    assert (row.status, row.last_error) == ("connected", None)
