"""The catalogue query, the field-allowlist parser and one search (ADR 0016 §4 and §5, B1).

No database and no network: the parser is pure, and ``search`` runs against ``CatalogueFake``
through the production catalogue client (``swedebeat_cloud_test_client``).
"""

import gzip
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from mowl import swedebeat

from cataloguefake import (
    ARTIST,
    FOUND_SECONDS,
    FOUND_TITLE,
    PLANTED_KEY,
    PLANTED_REFERENCE,
    PLANTED_URL,
    UNSURE_SECONDS,
    UNSURE_TITLE,
    CatalogueFake,
)
from classbeat.egress import SWEDEBEAT_CLOUD_HOST, OutboundBlocked, swedebeat_cloud_test_client
from classbeat.swedebeat_match import catalogue
from classbeat.swedebeat_match.search import SearchFailure, headers, search

TOKEN = "sbt-test-token-not-a-real-one"


def _url_text(title: str, artist: str) -> str:
    return unquote(catalogue.search_url(title, artist))


def test_the_query_carries_the_cleaned_title_and_artist_and_nothing_else() -> None:
    url = catalogue.search_url("Blue Monday - Remastered 2011", "New Order")
    parts = urlsplit(url)
    assert (parts.scheme, parts.hostname, parts.path) == (
        "https",
        SWEDEBEAT_CLOUD_HOST,
        "/track/extendedsearch",
    )
    assert set(parse_qs(parts.query)) == {"q", "q.parser", "start", "size"}
    text = unquote(url)
    assert "Blue Monday" in text
    assert "New Order" in text
    assert "Remastered" not in text
    assert "2011" not in text
    assert "spotify" not in text.lower()


def test_the_parser_keeps_title_artist_and_duration_and_drops_the_rest() -> None:
    planted_key = b"AKIA" + b"012345678901"
    planted_token = b"eyJ" + b"hbGciOiJIUzI1NiJ9.payload.sig"
    body = (
        b'{"data":{"hit":[{"reference":"sb-1","title":"Blue Monday","artist":'
        b'{"title":"New Order","id":"secret-artist"},"duration":241.4,'
        b'"url":"https://evil.example/a","accessKeyId":"' + planted_key + b'",'
        b'"stream":"http://cdn.example/a.mp3"}],"next":"https://evil.example/more"},'
        b'"token":"' + planted_token + b'"}'
    )
    hits = catalogue.parse_hits(body)
    assert hits is not None
    assert [(h.title, h.artist, h.duration) for h in hits] == [("Blue Monday", "New Order", 241)]
    kept = f"{hits[0]}"
    assert "sb-1" not in kept
    assert "evil.example" not in kept
    assert "AKIA" not in kept
    assert "eyJ" not in kept
    assert "secret-artist" not in kept


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b'{"data":{"hit":[{"title":"https://evil.example/a","artist":{"title":"A"}}]}}',
        b'{"data":{"hit":[{"title":"Fine","artist":{"title":"'
        + b"AKIA"
        + b"012345678901"
        + b'"}}]}}',
        b'{"data":{"hit":[{"title":"","artist":{"title":"A"}}]}}',
        b'{"data":{"hit":[{"title":"Fine","artist":"not-an-object"}]}}',
    ],
)
def test_a_hit_without_a_plain_title_and_artist_is_dropped_or_the_body_is_unreadable(
    body: bytes,
) -> None:
    hits = catalogue.parse_hits(body)
    if hits is None:
        assert body == b"not-json"
    else:
        assert hits == []


def test_duration_outside_a_song_length_is_not_kept() -> None:
    body = (
        b'{"hit":[{"title":"A","artist":{"title":"B"},"duration":0},'
        b'{"title":"C","artist":{"title":"D"},"duration":true},'
        b'{"title":"E","artist":{"title":"F"},"duration":99999}]}'
    )
    hits = catalogue.parse_hits(body)
    assert hits is not None
    assert [h.duration for h in hits] == [None, None, None]


def test_the_verdict_uses_the_library_threshold_and_unsure_is_not_found() -> None:
    found = catalogue.verdict(FOUND_TITLE, [catalogue.Hit(FOUND_TITLE, ARTIST, FOUND_SECONDS)])
    unsure = catalogue.verdict(
        UNSURE_TITLE, [catalogue.Hit("Bizarre Love Triangle", ARTIST, UNSURE_SECONDS)]
    )
    missing = catalogue.verdict("Not In The Catalogue", [])
    assert swedebeat.title_score(FOUND_TITLE, FOUND_TITLE) >= catalogue.SAME_SONG
    assert swedebeat.title_score(UNSURE_TITLE, "Bizarre Love Triangle") < catalogue.SAME_SONG
    assert (found.status, unsure.status, missing.status) == ("found", "unsure", "missing")
    assert found.hit is not None
    assert unsure.hit is not None
    assert missing.hit is None
    assert catalogue.SAME_SONG == 0.85


async def _search(fake: CatalogueFake, title: str = FOUND_TITLE) -> list[catalogue.Hit]:
    async with swedebeat_cloud_test_client(fake) as client:
        return await search(client, TOKEN, title, ARTIST)


async def test_a_search_sends_only_the_three_headers_and_returns_the_kept_hit() -> None:
    fake = CatalogueFake()
    hits = await _search(fake)
    assert [(h.title, h.artist, h.duration) for h in hits] == [(FOUND_TITLE, ARTIST, FOUND_SECONDS)]
    [call] = fake.calls
    assert call.method == "GET"
    assert call.token == TOKEN
    assert set(call.headers) <= {"accept", "user-agent", "x-auth-token", "host"}
    assert call.headers["user-agent"] == catalogue.USER_AGENT
    assert call.headers["accept"] == "application/json"
    assert headers(TOKEN)["X-Auth-Token"] == TOKEN
    text = unquote(call.url)
    assert FOUND_TITLE in text
    assert ARTIST in text
    assert PLANTED_REFERENCE not in text
    assert PLANTED_URL not in text
    assert PLANTED_KEY not in text


async def test_a_redirect_is_refused_and_the_token_is_not_sent_again() -> None:
    away = CatalogueFake(redirect="https://evil.example/steal")
    with pytest.raises(OutboundBlocked) as refused:
        await _search(away)
    assert refused.value.after_request is True
    assert len(away.calls) == 1
    assert urlsplit(away.calls[0].url).hostname == SWEDEBEAT_CLOUD_HOST
    assert away.calls[0].token == TOKEN
    assert "evil.example" not in away.calls[0].url

    same = CatalogueFake(redirect=f"https://{SWEDEBEAT_CLOUD_HOST}/track/next")
    with pytest.raises(OutboundBlocked) as refused:
        await _search(same)
    assert refused.value.after_request is True
    assert len(same.calls) == 1


@pytest.mark.parametrize(
    ("status", "category", "hint"),
    [
        (429, "rate_limited", 90),
        (503, "upstream_unavailable", None),
        (401, "needs_reconnect", None),
    ],
)
async def test_a_refused_search_is_one_category_and_one_request(
    status: int, category: str, hint: int | None
) -> None:
    fake = CatalogueFake(status=status, retry_after=None if hint is None else str(hint))
    with pytest.raises(SearchFailure) as raised:
        await _search(fake)
    assert (raised.value.category, raised.value.retry_after) == (category, hint)
    assert len(fake.calls) == 1
    assert fake.calls[0].token == TOKEN


async def test_a_compressed_or_oversized_body_is_not_parsed() -> None:
    payload = b'{"hit":[{"title":"Blue Monday","artist":{"title":"New Order"},"duration":241}]}'
    compressed = CatalogueFake(body=gzip.compress(payload), encoding="gzip")
    with pytest.raises(SearchFailure) as raised:
        await _search(compressed)
    assert raised.value.category == "invalid_response"

    huge = CatalogueFake(body=b'{"hit":[]}', encoding=None)
    huge_response_body = b"{" + b" " * (catalogue.MAX_ANSWER_BYTES + 1) + b"}"
    huge.body = huge_response_body
    with pytest.raises(SearchFailure) as raised:
        await _search(huge)
    assert raised.value.category == "invalid_response"


def test_the_query_of_a_song_does_not_include_its_spotify_id_or_bpm() -> None:
    text = _url_text("Blue Monday", "New Order")
    assert "Sp0tifyNeverSent" not in text
    assert "137.5" not in text
    assert "SecretBrief" not in text
