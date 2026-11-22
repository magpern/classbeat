"""The Spotify Connect audition through the API (ADR 0042 sections 7.2 and 7.3, #1395), against
the in-process fake Spotify. Never the network.

Covered: an audition plays only the current draft's song at ``piece_index``, from
``position_ms``, on a device of the server's own devices list; a forged track, uri or device id,
an index outside the draft, a position outside the track and a class without a current draft are
each refused before any Spotify request; another owner's class is 404; missing preview scopes are
the "Enable listening preview" refusal; each row of the section 7.3 state table; pause is sent
only while a song of the class plays; the player mode of ``spotify_call`` (no ``record_result``,
a refresh only inside the margin, a 401 refreshes and repeats once and flips only on the repeat's
401); Spotify's 403 and 429; and no token in any answer or log."""

import logging
from typing import Any

import pytest
from sqlalchemy.engine import Connection

from previewtools import (
    DEVICE,
    LENGTH_SEC,
    TRACKS,
    Preview,
    class_with_draft,
    draft,
    make_current,
    new_class,
    preview,
    song_list,
)
from spotifycore import API_HOST, SCOPES, track_id
from spotifyfake import USER_A
from spotifytools import Spot

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")


@pytest.fixture
async def pv(spot: Spot, spotify_admin: Connection) -> Preview:
    return await preview(spot, spotify_admin)


def api_requests(pv: Preview) -> list[tuple[str, str]]:
    return [(s.method, s.path) for s in pv.spot.fake.requests(API_HOST)]


def reason(response: Any) -> str:
    return response.json()["detail"]["reason"]


# --- audition ------------------------------------------------------------------------------


async def test_an_audition_plays_the_drafts_song_from_the_position_on_the_users_device(
    pv: Preview,
) -> None:
    pv.spot.fake.add_device(USER_A, "e2" * 20, "Phone", type="Smartphone")
    pv.spot.fake.add_device(USER_A, DEVICE, "Bike room", active=True)
    response = await pv.audition({"piece_index": 1, "position_ms": 30_000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "status": "starting",
        "piece_index": 1,
        "track_id": TRACKS[1],
        "position_ms": 30_000,
        "device": {"name": "Bike room", "type": "Computer"},
        "round_trip_ms": body["round_trip_ms"],
    }
    assert isinstance(body["round_trip_ms"], int)
    assert response.headers["cache-control"] == "no-store"
    assert api_requests(pv) == [("GET", "/v1/me/player/devices"), ("PUT", "/v1/me/player/play")]
    assert pv.spot.fake.player_bodies == [
        {"uris": [f"spotify:track:{TRACKS[1]}"], "position_ms": 30_000}
    ]
    assert pv.spot.fake.requests(API_HOST)[1].query == {"device_id": DEVICE}
    assert DEVICE not in response.text
    playing = pv.spot.fake.playback[USER_A]
    assert (playing.item, playing.device_id, playing.is_playing) == (TRACKS[1], DEVICE, True)


async def test_without_an_active_device_the_first_listed_one_is_used(pv: Preview) -> None:
    pv.spot.fake.add_device(USER_A, "r3" * 20, "Speaker", restricted=True)
    pv.spot.fake.add_device(USER_A, DEVICE, "Laptop")
    response = await pv.audition({"piece_index": 0, "position_ms": 0})
    assert response.status_code == 200, response.text
    assert pv.spot.fake.requests(API_HOST)[1].query == {"device_id": DEVICE}


@pytest.mark.parametrize(
    "body",
    [
        {"piece_index": 0, "position_ms": 0, "track_id": track_id(7)},
        {"piece_index": 0, "position_ms": 0, "uri": f"spotify:track:{track_id(7)}"},
        {"piece_index": 0, "position_ms": 0, "uris": [f"spotify:track:{track_id(7)}"]},
        {"piece_index": 0, "position_ms": 0, "device_id": "f0" * 20},
        {"piece_index": 0, "position_ms": 0, "context_uri": "spotify:album:x"},
        {"piece_index": "0", "position_ms": 0},
        {"piece_index": 0, "position_ms": 1.5},
        {"piece_index": True, "position_ms": 0},
        {"piece_index": -1, "position_ms": 0},
        {"piece_index": 0, "position_ms": -1},
        {"piece_index": 0},
        {"position_ms": 0},
        {},
    ],
)
async def test_a_forged_or_malformed_body_is_refused_before_any_request(
    pv: Preview, body: dict[str, Any]
) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    response = await pv.audition(body)
    assert response.status_code == 422
    assert api_requests(pv) == []


async def test_an_index_outside_the_draft_is_refused(pv: Preview) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    response = await pv.audition({"piece_index": len(TRACKS), "position_ms": 0})
    assert (response.status_code, reason(response)) == (422, "piece_out_of_range")
    assert api_requests(pv) == []


@pytest.mark.parametrize("position", [LENGTH_SEC * 1000, LENGTH_SEC * 1000 + 1, 7_200_000])
async def test_a_position_outside_the_track_is_refused(pv: Preview, position: int) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    response = await pv.audition({"piece_index": 0, "position_ms": position})
    assert (response.status_code, reason(response)) == (422, "position_out_of_range")
    assert api_requests(pv) == []


async def test_the_last_millisecond_of_the_track_plays(pv: Preview) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    response = await pv.audition({"piece_index": 2, "position_ms": LENGTH_SEC * 1000 - 1})
    assert response.status_code == 200, response.text


async def test_no_device_is_refused_and_nothing_is_played(pv: Preview) -> None:
    response = await pv.audition({"piece_index": 0, "position_ms": 0})
    assert (response.status_code, reason(response)) == (409, "no_device")
    assert api_requests(pv) == [("GET", "/v1/me/player/devices")]
    devices = await pv.get("devices")
    assert devices.status_code == 200
    assert devices.json()["status"] == "no_device"


async def test_the_devices_answer_names_and_types_only(pv: Preview) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, "Bike room", active=True)
    response = await pv.get("devices")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["devices"] == [{"name": "Bike room", "type": "Computer", "is_active": True}]
    assert body["target"] == {"name": "Bike room", "type": "Computer"}
    assert DEVICE not in response.text


# --- the gate ------------------------------------------------------------------------------


async def no_draft_classes(pv: Preview) -> list[Any]:
    admin, owner = pv.admin, pv.owner
    bare = new_class(admin, owner)  # no current revision
    on_list = new_class(admin, owner)  # the list is current (waiting for approval)
    make_current(admin, on_list, song_list(admin, owner, on_list, TRACKS))
    linked = new_class(admin, owner)  # a draft on a MOWL link, with no song list
    make_current(admin, linked, draft(admin, owner, linked, None))
    return [bare, on_list, linked]


async def test_a_class_without_a_current_spotify_draft_is_refused(pv: Preview) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    for class_id in await no_draft_classes(pv):
        audition = await pv.audition({"piece_index": 0, "position_ms": 0}, class_id)
        assert (audition.status_code, reason(audition)) == (409, "no_draft")
        state = await pv.get("state", class_id)
        assert (state.status_code, reason(state)) == (409, "no_draft")
        pause = await pv.actor.post(pv.url("pause", class_id))
        assert (pause.status_code, reason(pause)) == (409, "no_draft")
    assert api_requests(pv) == []


async def test_a_list_whose_hash_no_longer_holds_is_no_draft(pv: Preview) -> None:
    pv.admin.exec_driver_sql(
        "UPDATE class_revisions SET content = jsonb_set(content, '{note}', '\"x\"') "
        "WHERE kind = 'song_list' AND class_id = %(c)s",
        {"c": pv.class_id},
    )
    response = await pv.audition({"piece_index": 0, "position_ms": 0})
    assert (response.status_code, reason(response)) == (409, "no_draft")
    assert api_requests(pv) == []


async def test_another_owners_class_is_404_on_every_route(
    spot: Spot, spotify_admin: Connection, pv: Preview
) -> None:
    other = await spot.actor("someone-else@example.com")
    foreign = class_with_draft(spotify_admin, spot.user_id(other))
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    for class_id in (foreign, "not-a-uuid", "00000000-0000-0000-0000-000000000000"):
        audition = await pv.audition({"piece_index": 0, "position_ms": 0}, class_id)
        assert (audition.status_code, reason(audition)) == (404, "not_found")
        for what in ("state", "devices"):
            got = await pv.get(what, class_id)
            assert (got.status_code, reason(got)) == (404, "not_found")
        pause = await pv.actor.post(pv.url("pause", class_id))
        assert (pause.status_code, reason(pause)) == (404, "not_found")
    assert api_requests(pv) == []


async def test_missing_preview_scopes_are_the_enable_listening_preview_refusal(
    spot: Spot, spotify_admin: Connection
) -> None:
    pv = await preview(spot, spotify_admin, scope=SCOPES)
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    for response in (
        await pv.audition({"piece_index": 0, "position_ms": 0}),
        await pv.get("state"),
        await pv.get("devices"),
        await pv.actor.post(pv.url("pause")),
    ):
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "reason": "preview_not_granted",
            "action": "enable_listening_preview",
        }
    assert api_requests(pv) == []


async def test_without_a_connection_nothing_is_sent(spot: Spot, spotify_admin: Connection) -> None:
    pv = await preview(spot, spotify_admin)
    pv.admin.exec_driver_sql(
        "DELETE FROM connections WHERE owner_id = %(o)s AND provider = 'spotify'", {"o": pv.owner}
    )
    response = await pv.get("state")
    assert (response.status_code, reason(response)) == (409, "not_connected")
    assert api_requests(pv) == []


# --- the state table (section 7.3) ---------------------------------------------------------


async def state(pv: Preview) -> dict[str, Any]:
    response = await pv.get("state")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


async def test_nothing_playing_is_idle(pv: Preview) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    body = await state(pv)
    assert (body["status"], body["track_id"], body["device"]) == ("idle", None, None)


async def test_a_song_of_the_class_playing_then_paused(pv: Preview) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, "Bike room", active=True)
    assert (await pv.audition({"piece_index": 0, "position_ms": 5000})).status_code == 200
    body = await state(pv)
    assert body["status"] == "playing"
    assert (body["track_id"], body["pieces"], body["is_playing"]) == (TRACKS[0], [0, 2], True)
    assert body["progress_ms"] >= 5000
    assert isinstance(body["timestamp"], int)
    assert body["device"] == {"name": "Bike room", "type": "Computer"}
    paused = await pv.actor.post(pv.url("pause"))
    assert paused.status_code == 200, paused.text
    assert (paused.json()["status"], paused.json()["paused"]) == ("paused", True)
    after = await state(pv)
    assert (after["status"], after["is_playing"]) == ("paused", False)


async def test_another_item_is_external_and_its_id_is_not_passed_on(pv: Preview) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    pv.spot.fake.play_external(USER_A, track_id(9))
    body = await state(pv)
    assert (body["status"], body["track_id"], body["pieces"]) == ("external", None, [])
    pv.spot.fake.play_external(USER_A, kind="episode")
    assert (await state(pv))["status"] == "external"
    # Pause is not a remote control: nothing of the class plays, so nothing is sent.
    paused = await pv.actor.post(pv.url("pause"))
    assert paused.status_code == 200
    assert (paused.json()["status"], paused.json()["paused"]) == ("external", False)
    assert ("PUT", "/v1/me/player/pause") not in api_requests(pv)
    assert track_id(9) not in paused.text


async def test_a_new_device_still_plays_and_is_named(pv: Preview) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, "Bike room", active=True)
    assert (await pv.audition({"piece_index": 1, "position_ms": 0})).status_code == 200
    phone = pv.spot.fake.add_device(USER_A, "p4" * 20, "Phone", type="Smartphone")
    pv.spot.fake.playback[USER_A].device_id = phone.id  # the user moved the playback
    body = await state(pv)
    assert (body["status"], body["device"]) == ("playing", {"name": "Phone", "type": "Smartphone"})


# --- the player mode of spotify_call -------------------------------------------------------


async def test_polls_never_refresh_a_fresh_token_and_record_nothing(pv: Preview) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    for _ in range(3):
        await state(pv)
    assert pv.spot.token_requests() == []
    row = pv.connection()
    assert (row.status, row.last_error, row.last_used_at) == ("connected", None, None)


async def test_a_token_inside_the_margin_is_refreshed_once(
    spot: Spot, spotify_admin: Connection
) -> None:
    pv = await preview(spot, spotify_admin, expires_in=30)
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    for _ in range(3):
        await state(pv)
    assert len(pv.spot.token_requests()) == 1
    assert pv.connection().last_used_at is None


async def test_a_401_refreshes_and_repeats_once(pv: Preview) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    pv.spot.fake.expire(pv.access)  # Spotify refuses the stored token before its expiry
    response = await pv.audition({"piece_index": 0, "position_ms": 0})
    assert response.status_code == 200, response.text
    assert len(pv.spot.token_requests()) == 1
    assert api_requests(pv) == [
        ("GET", "/v1/me/player/devices"),
        ("GET", "/v1/me/player/devices"),
        ("PUT", "/v1/me/player/play"),
    ]
    assert pv.connection().status == "connected"


async def test_a_401_on_the_repeat_flips_to_needs_reconnect(pv: Preview) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    pv.spot.fake.inject("status", status=401, path="/v1/me/player", times=2)
    response = await pv.get("state")
    assert (response.status_code, reason(response)) == (409, "needs_reconnect")
    assert len(pv.spot.token_requests()) == 1
    assert api_requests(pv) == [("GET", "/v1/me/player"), ("GET", "/v1/me/player")]
    row = pv.connection()
    assert (row.status, row.last_error) == ("needs_reconnect", "invalid_grant")
    again = await pv.get("state")
    assert (again.status_code, reason(again)) == (409, "needs_reconnect")


async def test_a_play_refused_401_once_is_sent_once_more_and_never_flips(pv: Preview) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    pv.spot.fake.inject("status", status=401, path="/v1/me/player/play", times=1)
    response = await pv.audition({"piece_index": 0, "position_ms": 0})
    assert response.status_code == 200, response.text
    plays = [r for r in api_requests(pv) if r == ("PUT", "/v1/me/player/play")]
    assert len(plays) == 2
    assert pv.connection().status == "connected"


async def test_spotify_403_is_premium_or_the_allowlist_and_never_flips(pv: Preview) -> None:
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    pv.spot.fake.free_users.add(USER_A)
    response = await pv.audition({"piece_index": 0, "position_ms": 0})
    assert (response.status_code, reason(response)) == (409, "premium_or_allowlist")
    pv.spot.fake.inject("status", status=403, path="/v1/me/player")
    got = await pv.get("state")
    assert (got.status_code, reason(got)) == (409, "premium_or_allowlist")
    row = pv.connection()
    assert (row.status, row.last_error) == ("connected", None)


async def test_spotify_429_answers_its_retry_after(pv: Preview) -> None:
    pv.spot.fake.inject("rate_limited", path="/v1/me/player", retry_after=7)
    response = await pv.get("state")
    assert response.status_code == 429
    assert response.json()["detail"] == {"reason": "rate_limited", "retry_after": 7}
    assert response.headers["retry-after"] == "7"
    assert pv.connection().status == "connected"


async def test_a_spotify_outage_is_upstream_unavailable(pv: Preview) -> None:
    pv.spot.fake.inject("status", status=503, path="/v1/me/player/devices")
    response = await pv.get("devices")
    assert (response.status_code, reason(response)) == (503, "upstream_unavailable")
    pv.spot.fake.inject("refuse", path="/v1/me/player/devices")
    response = await pv.get("devices")
    assert (response.status_code, reason(response)) == (503, "upstream_unavailable")


async def test_no_token_in_any_answer_or_log(pv: Preview, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    pv.spot.fake.add_device(USER_A, DEVICE, active=True)
    pv.spot.fake.expire(pv.access)  # a refresh too, so the new tokens exist
    answers = [
        await pv.audition({"piece_index": 0, "position_ms": 0}),
        await pv.get("state"),
        await pv.get("devices"),
        await pv.actor.post(pv.url("pause")),
    ]
    pv.spot.fake.inject("status", status=401, path="/v1/me/player", times=2)
    answers.append(await pv.get("state"))
    tokens = {pv.access, pv.refresh, *pv.spot.fake.access, *pv.spot.fake.refresh}
    assert len(tokens) >= 4
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for token in tokens:
        for answer in answers:
            assert token not in answer.text
            assert token not in str(answer.headers)
        assert token not in logged
