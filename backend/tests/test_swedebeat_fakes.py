"""A1 of ADR 0016: the MOWL fake's Swedebeat calls reproduce what the library makes (``mowl.api``
against ``mowlfake``), and the fake media endpoint's failure injection reaches the dedicated media
client (``egress.mowl_media_async_client``) with an injected list. In-process, never the network."""

import http.client
import io
import time
import urllib.error
import urllib.parse
import wave
from typing import Any

import httpx
import pytest
from mowl import api
from mowl.credentials import Credentials

from classbeat.egress import (
    MOWL_MEDIA_ALLOWLIST,
    Allowlist,
    OutboundBlocked,
    RequestDeadline,
    mowl_media_async_client,
)
from mediafake import OTHER_HOST, FakeMedia
from mowlfake import TOKEN, FakeMowl

pytestmark = pytest.mark.usefixtures("mowl_api")
OTHER_TOKEN = "00000000-1111-2222-3333-444444444444"  # another account (a public test value)


def creds(token: str = TOKEN) -> Credentials:
    return Credentials(mowl_token=token)


def closed(refused: "pytest.ExceptionInfo[api.MowlError]") -> api.MowlError:
    """The refusal, with the HTTP error under it closed (its socket would be collected later)."""
    cause = refused.value.__cause__
    if isinstance(cause, urllib.error.HTTPError):
        cause.close()
    return refused.value


def client(media: FakeMedia, **kwargs: Any) -> httpx.AsyncClient:
    return mowl_media_async_client(Allowlist(frozenset({FakeMedia.HOST})), inner=media, **kwargs)


# --- the library's calls against the fake MOWL ----------------------------------------------


def test_the_listing_is_the_callers_own_and_filters_by_name(mowl_api: FakeMowl) -> None:
    mine = mowl_api.add_mix("Sunday Climb", listed_seconds=300)
    mowl_api.add_mix("Other Account Mix", token=OTHER_TOKEN)
    listed = api.swedebeat_mixes(creds())
    assert [m["ID"] for m in listed] == [mine]
    assert listed[0]["Name"] == "Sunday Climb"
    assert listed[0]["DurationInMilliseconds"] == 300_000
    assert api.swedebeat_mixes(creds(), "nothing") == []
    assert [m["Name"] for m in api.swedebeat_mixes(creds(OTHER_TOKEN))] == ["Other Account Mix"]
    assert api.find_swedebeat(creds(), "sunday climb")["ID"] == mine


def test_a_call_without_a_known_token_is_refused(mowl_api: FakeMowl) -> None:
    mowl_api.add_mix("Mix")
    with pytest.raises(api.MowlError) as refused:
        api.swedebeat_mixes(creds("ffffffff-1111-2222-3333-444444444444"))
    assert closed(refused).status == 401


def test_attach_wraps_the_mix_once_and_only_for_its_owner(mowl_api: FakeMowl) -> None:
    mix = mowl_api.add_mix("Mix")
    mowl_api.add_mix("Theirs", token=OTHER_TOKEN)
    first = api.attach_swedebeat(creds(), mix)["PlaylistID"]
    assert api.attach_swedebeat(creds(), mix)["PlaylistID"] == first  # a repeat is a no-op
    with pytest.raises(api.MowlError, match="Unknown"):
        api.attach_swedebeat(creds(OTHER_TOKEN), mix)  # not in that account's listing
    with pytest.raises(api.MowlError, match="Unknown"):
        api.attach_swedebeat(creds(), "made-up-id")
    assert mowl_api.attaches == [mix, mix]  # only the two real attaches count


def test_indexing_takes_polls_and_the_library_waits_for_it(
    mowl_api: FakeMowl, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    mix = mowl_api.add_mix("Slow", index_polls=2, audio_seconds=61.5)
    pid = api.attach_swedebeat(creds(), mix)["PlaylistID"]
    track = api.wait_indexed(creds(), pid)
    assert track["IndexStatus"] == "Track indexing done"
    assert track["Duration"] == 61_500
    assert mowl_api.polls[pid] == 3


def test_an_indexing_error_reaches_the_library(
    mowl_api: FakeMowl, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    mix = mowl_api.add_mix("Broken", index_error="cannot decode")
    pid = api.attach_swedebeat(creds(), mix)["PlaylistID"]
    with pytest.raises(api.MowlError, match="cannot decode"):
        api.wait_indexed(creds(), pid)


def test_tracks_and_links_are_for_an_attached_mix_of_the_caller(mowl_api: FakeMowl) -> None:
    mix = mowl_api.add_mix("Mix")
    mowl_api.add_mix("Theirs", token=OTHER_TOKEN)
    pid = api.attach_swedebeat(creds(), mix)["PlaylistID"]
    path = f"v1/swedebeat/playlist/{pid}/TemporaryLinks"
    link = api.call(creds(), "GET", path)["TemporaryLinks"][0]["TemporaryLink"]
    assert urllib.parse.urlsplit(link).hostname == FakeMedia.HOST
    with pytest.raises(api.MowlError) as refused:
        api.call(creds(OTHER_TOKEN), "GET", path)
    assert closed(refused).status == 404
    for token, playlist in ((OTHER_TOKEN, pid), (TOKEN, 1)):  # not theirs; never attached
        with pytest.raises(api.MowlError) as tracks:
            api.playlist_tracks(creds(token), playlist)
        assert closed(tracks).status == 404


# --- A2: what the attach tests need from the fake --------------------------------------------


def test_the_same_mix_id_can_belong_to_two_accounts(mowl_api: FakeMowl) -> None:
    mine = mowl_api.add_mix("Mine", mix_id="shared-1")
    theirs = mowl_api.add_mix("Theirs", token=OTHER_TOKEN, mix_id="shared-1")
    assert mine == theirs == "shared-1"
    assert [m["Name"] for m in api.swedebeat_mixes(creds())] == ["Mine"]
    assert [m["Name"] for m in api.swedebeat_mixes(creds(OTHER_TOKEN))] == ["Theirs"]
    a = api.attach_swedebeat(creds(), "shared-1")["PlaylistID"]
    b = api.attach_swedebeat(creds(OTHER_TOKEN), "shared-1")["PlaylistID"]
    assert a != b  # each account has its own MOWL playlist for it
    assert api.playlist_tracks(creds(), a)
    with pytest.raises(api.MowlError) as refused:
        api.playlist_tracks(creds(), b)  # the other account's playlist
    assert closed(refused).status == 404


def test_the_listing_shows_the_playlist_of_an_attached_mix_to_its_caller_only(
    mowl_api: FakeMowl,
) -> None:
    mix = mowl_api.add_mix("Mix", mix_id="shared-1")
    mowl_api.add_mix("Theirs", token=OTHER_TOKEN, mix_id="shared-1")
    assert "PlaylistID" not in api.swedebeat_mixes(creds())[0]
    pid = api.attach_swedebeat(creds(), mix)["PlaylistID"]
    assert api.swedebeat_mixes(creds())[0]["PlaylistID"] == pid
    assert "PlaylistID" not in api.swedebeat_mixes(creds(OTHER_TOKEN))[0]
    mowl_api.list_shows_playlist = False
    assert "PlaylistID" not in api.swedebeat_mixes(creds())[0]


@pytest.mark.parametrize("endpoint", ["list", "attach", "tracks", "links"])
@pytest.mark.parametrize("code", [401, 429, 503])
def test_a_failure_switch_hits_one_endpoint_only(
    mowl_api: FakeMowl, endpoint: str, code: int
) -> None:
    mix = mowl_api.add_mix("Mix")
    pid = api.attach_swedebeat(creds(), mix)["PlaylistID"]
    mowl_api.retry_after = "9"
    mowl_api.status[f"swedebeat_{endpoint}"] = code
    calls = {
        "list": lambda: api.swedebeat_mixes(creds()),
        "attach": lambda: api.attach_swedebeat(creds(), mix),
        "tracks": lambda: api.playlist_tracks(creds(), pid),
        "links": lambda: api.call(creds(), "GET", f"v1/swedebeat/playlist/{pid}/TemporaryLinks"),
    }
    for name, call in calls.items():
        if name == endpoint:
            with pytest.raises(api.MowlError) as refused:
                call()
            assert closed(refused).status == code
            if code == 429:
                assert isinstance(refused.value.__cause__, urllib.error.HTTPError)
                assert refused.value.__cause__.headers["Retry-After"] == "9"
        else:
            call()  # the other endpoints still answer
    mowl_api.status.clear()
    mowl_api.status["swedebeat"] = 503  # the old switch still covers all four
    for call in calls.values():
        with pytest.raises(api.MowlError) as refused:
            call()
        assert closed(refused).status == 503


def test_the_attach_can_happen_and_its_answer_be_lost_or_garbled(mowl_api: FakeMowl) -> None:
    mix = mowl_api.add_mix("Mix")
    mowl_api.attach_answer = "garbled"
    assert "PlaylistID" not in api.attach_swedebeat(creds(), mix)
    mowl_api.attach_answer = "drop"
    with pytest.raises((api.MowlError, http.client.HTTPException, OSError)):
        api.attach_swedebeat(creds(), mix)  # the connection is cut before the answer
    assert mowl_api.attaches == [mix, mix]  # it happened both times
    mowl_api.attach_answer = "ok"
    assert (
        api.swedebeat_mixes(creds())[0]["PlaylistID"]
        == api.attach_swedebeat(creds(), mix)["PlaylistID"]
    )


# --- the media client against the fake media endpoint ---------------------------------------


async def fetch(media: FakeMedia, link: str, **kwargs: Any) -> httpx.Response:
    async with client(media, **kwargs) as http:
        return await http.get(link)


async def test_a_good_link_gives_the_generated_audio() -> None:
    media = FakeMedia()
    answer = await fetch(media, media.link("ok", seconds=1.0))
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(answer.content)) as audio:
        assert audio.getnframes() == 8000


async def test_octet_stream_is_still_audio() -> None:
    media = FakeMedia()
    answer = await fetch(media, media.link("octet"))
    assert answer.headers["content-type"] == "application/octet-stream"
    with wave.open(io.BytesIO(answer.content)) as audio:
        assert audio.getnframes() > 0


async def test_an_undecodable_file_is_served_as_audio() -> None:
    media = FakeMedia()
    answer = await fetch(media, media.link("undecodable"))
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "audio/mpeg"
    with pytest.raises(wave.Error):
        wave.open(io.BytesIO(answer.content))  # noqa: SIM115


@pytest.mark.parametrize("code", [403, 404, 410, 500])
async def test_an_error_status_comes_back_as_is(code: int) -> None:
    media = FakeMedia()
    assert (await fetch(media, media.link("status", code=code))).status_code == code


async def test_a_same_host_redirect_is_not_followed() -> None:
    media = FakeMedia()
    answer = await fetch(media, media.link("redirect"))
    assert answer.status_code == 302  # handed back for the caller to refuse
    assert [path.rsplit("/", 1)[1] for _m, _h, path in media.requests] == ["mix.mp3"]


async def test_a_redirect_to_another_host_is_refused_and_never_reached() -> None:
    media = FakeMedia()
    with pytest.raises(OutboundBlocked) as refused:
        await fetch(media, media.link("redirect_away"))
    assert refused.value.after_request is True
    assert {host for _m, host, _p in media.requests} == {FakeMedia.HOST}


async def test_an_oversized_body_declares_and_streams_its_size() -> None:
    media = FakeMedia()
    size = 5 * (1 << 20)
    async with client(media) as http:
        async with http.stream("GET", media.link("oversize", size=size)) as answer:
            assert int(answer.headers["content-length"]) == size
            count = sum([len(c) async for c in answer.aiter_bytes()])
        assert count == size
        async with http.stream("GET", media.link("oversize_stream", size=size)) as answer:
            assert "content-length" not in answer.headers
            count = 0
            async for chunk in answer.aiter_bytes():
                count += len(chunk)
                if count > size // 2:
                    break  # a reader stops at its cap without holding the rest
    assert count > size // 2


async def test_a_slow_body_ends_at_the_total_deadline() -> None:
    media = FakeMedia()
    started = time.monotonic()
    with pytest.raises(RequestDeadline):
        await fetch(media, media.link("slow", delay=0.05, chunks=1000), total_seconds=0.3)
    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    "link",
    [
        f"http://{FakeMedia.HOST}/media/x/mix.mp3",
        f"https://{FakeMedia.HOST}:8443/media/x/mix.mp3",
        f"https://user:pw@{FakeMedia.HOST}/media/x/mix.mp3",
        f"https://{FakeMedia.HOST}.evil.example/media/x/mix.mp3",
        f"https://sub.{FakeMedia.HOST}/media/x/mix.mp3",
        f"https://{OTHER_HOST}/media/x/mix.mp3",
        "https://169.254.169.254/latest/meta-data",
    ],
)
async def test_a_link_off_the_injected_list_is_refused_before_it_is_sent(link: str) -> None:
    media = FakeMedia()
    with pytest.raises(OutboundBlocked):
        await fetch(media, link)
    assert media.requests == []


async def test_the_production_list_is_empty_and_refuses_every_host() -> None:
    media = FakeMedia()
    assert MOWL_MEDIA_ALLOWLIST.hosts == frozenset()
    async with mowl_media_async_client(inner=media) as http:
        with pytest.raises(OutboundBlocked):
            await http.get(media.link("ok"))
    assert media.requests == []


async def test_the_request_carries_none_of_our_headers() -> None:
    media = FakeMedia()
    await fetch(media, media.link("ok"))
    (sent,) = media.headers
    assert set(sent) <= {"host"}  # no authorization, user-agent, accept or cookie
    assert "sig=" not in repr(media.requests)  # the record never holds the query


async def test_the_fake_flow_leaves_no_signature_in_the_records(mowl_api: FakeMowl) -> None:
    mix = mowl_api.add_mix("Mix")
    pid = api.attach_swedebeat(creds(), mix)["PlaylistID"]
    link = api.call(creds(), "GET", f"v1/swedebeat/playlist/{pid}/TemporaryLinks")
    url = link["TemporaryLinks"][0]["TemporaryLink"]
    assert (await fetch(mowl_api.media, url)).status_code == 200
    seen = repr((mowl_api.media.requests, mowl_api.requests))
    assert mowl_api.media.signatures
    assert all(s not in seen for s in mowl_api.media.signatures)
