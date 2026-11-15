"""``swedebeat_match`` on the fake gateway (ADR 0035 sections 5 and 6; ADR 0016 §5 and §8, B1).

A real ``song_list``, a real SwedeBeat connection (the fake gateway's login issues the bearer; the
job opens it through ``UserContext``) and the gateway client behind ``swedebeat_gateway.opener``
pointed at ``gatewayfake``. No network, no live account. Two users' bearers never appear on each
other's requests, and a request body carries only titles and artists (every body is read).
"""

import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import ORIGIN, Env, running_env, session_cookie
from classbeat import auth, swedebeat_match
from classbeat.connections import CATEGORIES as CONNECTION_CATEGORIES
from classbeat.connections.swedebeat_account import open_bearer
from classbeat.db import user_session
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.errors import CATEGORIES as JOB_CATEGORIES
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.songs.song_list import (
    Song,
    SongListContent,
    SongListMusic,
    content_hash,
    fingerprint,
)
from classbeat.swedebeat_match import gateway_match
from classbeat.swedebeat_match import kind as match_kind
from classbeat.swedebeat_match.config import MatchConfig
from classbeat.swedebeat_match.kind import SWEDEBEAT_MATCH
from ctx import make_context
from gatewaycore import (
    MATCH_PATH,
    SECONDS,
    SESSION_PATH,
    UNSURE_TITLE,
    FakeGateway,
    reference_of,
)
from gatewayfake import CLIENT_KEY, EMAIL, EMAIL_B, GATEWAY_SETTINGS, PASSWORD, PASSWORD_B, calls
from gatewayfake import ORIGIN as GATEWAY_ORIGIN
from jobtools import engine_for
from mfatools import Actor
from preftools import use_swedebeat

REFUSED_KEY = "swedebeat gateway refused the client key"
pytestmark = pytest.mark.usefixtures("gateway_api")

FOUND_TITLE = "Blue Monday"
UNSURE_ASKED = "Bizarre"
ARTIST = "New Order"
MISSING_TITLE = "Not In The Catalogue"
# What a match request carries: the client's own headers and the four the call sets.
SENT_HEADERS = {"host", "content-length", "accept", "content-type", "x-api-key", "authorization"}


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url, swedebeat_enabled=True, **GATEWAY_SETTINGS) as running:
        yield running


@pytest.fixture
def admin(database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


@pytest.fixture
def catalogue(gateway_api: FakeGateway) -> FakeGateway:
    """The fake gateway with one ``found`` and one ``unsure`` song; anything else is missing."""
    gateway_api.add_track(FOUND_TITLE, ARTIST, "found")
    gateway_api.add_track(UNSURE_ASKED, ARTIST, "unsure")
    return gateway_api


@pytest.fixture
def paused(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The match's pauses (the 401 confirmation's and the all-missing repeat's), not slept."""
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(gateway_match, "_pause", record)
    return slept


def match_config(env: Env, **more: Any) -> MatchConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "keyring": env.app.state.keyring,
        "swedebeat_gateway_url": GATEWAY_ORIGIN,
        "swedebeat_gateway_key": SecretStr(CLIENT_KEY),
        **more,
    }
    return MatchConfig(**values)


@pytest.fixture
def configured(env: Env, paused: list[float]) -> Iterator[None]:
    swedebeat_match.configure(match_config(env))
    yield
    swedebeat_match.configure(MatchConfig.from_settings(env.settings))


def _spotify_id(n: int) -> str:
    text_id = f"Sp0tifyNeverSent{n:06d}"
    assert len(text_id) == 22
    return text_id


def _song(n: int, title: str, artist: str, bpm: float) -> Song:
    return Song(
        spotify_id=_spotify_id(n),
        title=title,
        artist=artist,
        role="climb",
        bpm=bpm,
        length_sec=240,
        new=False,
    )


def _songs(start: int, bpm: float) -> list[Song]:
    return [
        _song(start, FOUND_TITLE, ARTIST, bpm),
        _song(start + 1, UNSURE_ASKED, ARTIST, bpm),
        _song(start + 2, MISSING_TITLE, "Nobody", bpm),
        _song(start + 3, FOUND_TITLE, ARTIST, bpm),
    ]


@dataclass
class Rider:
    user_id: UUID
    actor: Actor
    connection_id: UUID
    bearer: str
    class_id: UUID
    revision_id: UUID
    list_hash: bytes
    spotify_ids: list[str]
    bpm: float
    class_title: str


async def _plant(
    env: Env, user_id: UUID, title: str, songs: list[Song]
) -> tuple[UUID, UUID, bytes]:
    content = SongListContent(
        v=2,
        mode="pick",
        target_sec=960,
        total_sec=960,
        crossfade_sec=0.5,
        songs=songs,
        omitted_no_bpm=0,
        note="",
    )
    dumped = content.model_dump(mode="json", exclude_none=True)
    digest = content_hash(dumped)
    music = SongListMusic(
        source="claude_pick",
        fingerprint=fingerprint(tuple(song.spotify_id for song in songs)),
        spotify_user_id="spotify-user-test",
    ).model_dump(mode="json", exclude_none=True)
    class_id, revision_id = uuid4(), uuid4()
    async with user_session(env.factory, make_context(user_id)) as db:
        await db.execute(
            text("INSERT INTO classes (id, owner_id, title) VALUES (:id, :o, :t)"),
            {"id": class_id, "o": user_id, "t": title},
        )
        await db.execute(
            text(
                "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
                "content_hash, music, provenance) VALUES (:id, :o, :c, 1, 'song_list', "
                "CAST(:content AS jsonb), :hash, CAST(:music AS jsonb), '{}'::jsonb)"
            ),
            {
                "id": revision_id,
                "o": user_id,
                "c": class_id,
                "content": json.dumps(dumped),
                "hash": digest,
                "music": json.dumps(music),
            },
        )
    return class_id, revision_id, digest


async def _post_job(actor: Actor, payload: dict[str, Any]) -> Any:
    info = await actor.client.get("/api/auth/session", headers=session_cookie(actor.token))
    headers = {
        "Origin": ORIGIN,
        "X-CSRF-Token": info.json()["csrf_token"],
        "Idempotency-Key": secrets.token_urlsafe(18),
        **session_cookie(actor.token),
    }
    response = await actor.client.request("POST", "/api/jobs", headers=headers, json=payload)
    actor.client.cookies.clear()
    for cookie in response.headers.get_list("set-cookie"):
        if cookie.startswith(f"{auth.SESSION_COOKIE}=") and "Max-Age=0" not in cookie:
            actor.token = cookie.split(";")[0].split("=", 1)[1]
    return response


async def _connect(actor: Actor, email: str, password: str) -> UUID:
    response = await actor.post(
        "/api/connections/swedebeat", {"email": email, "password": password, "force": True}
    )
    assert response.status_code == 200, response.text
    return UUID(response.json()["id"])


async def _rider(
    env: Env,
    make_actor: Any,
    *,
    account: str,
    swedebeat_email: str,
    password: str,
    start: int,
    bpm: float,
    class_title: str,
    songs: list[Song] | None = None,
) -> Rider:
    user_id = await env.user(account)
    await use_swedebeat(env.factory, user_id)  # #525: the user's own switch
    actor = await make_actor(user_id)
    connection_id = await _connect(actor, swedebeat_email, password)
    listed = songs if songs is not None else _songs(start, bpm)
    class_id, revision_id, digest = await _plant(env, user_id, class_title, listed)
    opened = await open_bearer(env.factory, env.app.state.keyring, make_context(user_id))
    return Rider(
        user_id,
        actor,
        connection_id,
        opened.bearer,
        class_id,
        revision_id,
        digest,
        [s.spotify_id for s in listed],
        bpm,
        class_title,
    )


async def _one(env: Env, make_actor: Any, name: str, start: int, **more: Any) -> Rider:
    return await _rider(
        env, make_actor, account=f"{name}@example.com", swedebeat_email=EMAIL, password=PASSWORD,
        start=start, bpm=100.0 + start / 10, class_title=f"{name} brief", **more,
    )  # fmt: skip


def _body(rider: Rider) -> dict[str, Any]:
    return {
        "kind": "swedebeat_match",
        "input": {
            "class_id": str(rider.class_id),
            "song_list_revision_id": str(rider.revision_id),
            "swedebeat_connection_id": str(rider.connection_id),
        },
    }


async def _run(env: Env) -> UUID:
    registry = KindRegistry()
    registry.register(SWEDEBEAT_MATCH)
    lease = int(env.settings.job_lease_seconds)
    claimed = await queue.claim(env.factory, "b1-match", lease, ["swedebeat_match"])
    assert claimed is not None
    await execute(
        Execution(claimed), env.factory, registry, RunConfig(lease_seconds=lease), asyncio.Event()
    )
    return claimed.job_id


def _job(admin: Connection, job_id: UUID) -> Any:
    return admin.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).one()


def _match(admin: Connection, job_id: UUID) -> Any:
    return admin.execute(
        text(
            "SELECT content, music, provenance, parent_id FROM class_revisions "
            "WHERE job_id = :id AND kind = 'swedebeat_match'"
        ),
        {"id": job_id},
    ).one()


def _held(admin: Connection, rider: Rider) -> Any:
    return admin.execute(
        text(
            "SELECT status, last_error, last_used_at FROM connections "
            "WHERE owner_id = :o AND provider = 'swedebeat'"
        ),
        {"o": rider.user_id},
    ).one()


def _stored(admin: Connection, job_id: UUID) -> int:
    return admin.execute(
        text("SELECT count(*) FROM class_revisions WHERE job_id = :id"), {"id": job_id}
    ).scalar_one()


def _event_categories(admin: Connection, job_id: UUID) -> list[Any]:
    return [
        event.payload.get("category")
        for event in admin.execute(
            text("SELECT payload FROM job_events WHERE job_id = :id"), {"id": job_id}
        )
        if isinstance(event.payload, dict)
    ]


def _waits(admin: Connection, job_id: UUID) -> float:
    return float(
        admin.execute(
            text("SELECT extract(epoch FROM (available_at - now())) FROM jobs WHERE id = :id"),
            {"id": job_id},
        ).scalar_one()
    )


def _bodies_carry_titles_only(requests: list[Any], rider: Rider) -> None:
    """Every match request: the deduplicated titles and artists, nothing else of the class."""
    forbidden = [
        *rider.spotify_ids,
        str(rider.bpm),
        rider.class_title,
        str(rider.class_id),
        str(rider.user_id),
        "spotify",
        "climb",
        "240",
    ]
    assert requests
    for request in requests:
        assert request.method == "POST"
        assert request.body == {
            "tracks": [
                {"title": FOUND_TITLE, "artist": ARTIST},
                {"title": UNSURE_ASKED, "artist": ARTIST},
                {"title": MISSING_TITLE, "artist": "Nobody"},
            ],
            "include_unsure": False,
        }
        dumped = json.dumps(request.body).lower()
        for secret in forbidden:
            assert str(secret).lower() not in dumped
        assert set(request.headers) == SENT_HEADERS
        assert request.headers["authorization"] == f"Bearer {rider.bearer}"
        assert request.headers["x-api-key"] == CLIENT_KEY
        assert request.email is not None


async def test_two_users_match_with_one_call_each_and_their_bearers_never_mix(
    env: Env, admin: Connection, make_actor: Any, catalogue: FakeGateway, configured: None
) -> None:
    alice = await _rider(
        env,
        make_actor,
        account="alice@example.com",
        swedebeat_email=EMAIL,
        password=PASSWORD,
        start=1,
        bpm=137.5,
        class_title="SecretBriefPhraseQ9",
    )
    bob = await _rider(
        env,
        make_actor,
        account="bob@example.com",
        swedebeat_email=EMAIL_B,
        password=PASSWORD_B,
        start=11,
        bpm=88.5,
        class_title="OtherSecretBriefR2",
    )
    assert alice.bearer != bob.bearer
    for rider in (alice, bob):
        created = await _post_job(rider.actor, _body(rider))
        assert created.status_code == 201, created.text
    alice_job, bob_job = await _run(env), await _run(env)
    matches = calls(catalogue, MATCH_PATH)
    # One call per job (four songs, three distinct), each with its own user's bearer.
    assert [r.email for r in matches] == [EMAIL, EMAIL_B]
    _bodies_carry_titles_only(matches[:1], alice)
    _bodies_carry_titles_only(matches[1:], bob)
    assert calls(catalogue, SESSION_PATH) == []
    for rider, job_id in ((alice, alice_job), (bob, bob_job)):
        row = _job(admin, job_id)
        assert (row.state, row.error_category, row.owner_id) == ("completed", None, rider.user_id)
        match = _match(admin, job_id)
        content = match.content
        assert [song["status"] for song in content["songs"]] == [
            "found",
            "unsure",
            "missing",
            "found",
        ]
        assert (content["found"], content["unsure"], content["missing"]) == (2, 1, 1)
        assert content["songs"][0]["swedebeat"] == {
            "title": FOUND_TITLE, "artist": ARTIST, "duration": SECONDS,
            "reference": reference_of(FOUND_TITLE, ARTIST), "bpm": 128.0,
        }  # fmt: skip
        assert content["songs"][1]["swedebeat"]["title"] == UNSURE_TITLE
        assert content["songs"][2]["swedebeat"] is None
        assert content["swedebeat_sec"] == SECONDS * 2  # an unsure song is never counted
        # SR C2 (ADR 0036 section 3.4): a hit keeps its catalogue reference, a missing song none.
        unsure = content["songs"][1]
        assert unsure["swedebeat"]["reference"] == reference_of(unsure["wanted"]["title"], ARTIST)
        blob = json.dumps(content)
        assert blob.count('"reference"') == 3
        for spotify_id in rider.spotify_ids:
            assert spotify_id not in blob
        assert match.music == {
            "source": "swedebeat_catalogue",
            "song_list_hash": rider.list_hash.hex(),
        }
        assert match.provenance["origin"] == "swedebeat_match"
        assert match.provenance["threshold"] == 0.85
        assert match.provenance["matcher_version"] == 2  # the gateway's score, ADR 0035
        assert match.parent_id == rider.revision_id
        held = _held(admin, rider)
        assert (held.status, held.last_error) == ("connected", None)
        assert held.last_used_at is not None


def _item(status: str, score: float, track: Any) -> dict[str, Any]:
    return {"title": "asked", "artist": "asked", "status": status, "score": score, "track": track}


def _track(title: str, **more: Any) -> dict[str, Any]:
    return {"reference": "planted-ref-Kx1", "title": title, "artist": ARTIST, "bpm": 128.0, **more}


async def test_classbeat_decides_found_at_085_and_drops_every_other_field(
    env: Env, admin: Connection, make_actor: Any, catalogue: FakeGateway, configured: None
) -> None:
    songs = [
        _song(301, "Sure Song", ARTIST, 120.0),
        _song(302, "Loose Song", ARTIST, 120.0),
        _song(303, "Unsure Song", ARTIST, 120.0),
        _song(304, "No Track Song", ARTIST, 120.0),
        _song(305, "Gone Song", ARTIST, 120.0),
        _song(306, "Zero Length", ARTIST, 120.0),
        _song(307, "   ", ARTIST, 120.0),  # empty after trimming: not sent, missing
        _song(308, "No Artist", "", 120.0),
        _song(309, "Bad Reference", ARTIST, 120.0),
    ]
    rider = await _one(env, make_actor, "verdict", 300, songs=songs)
    planted_url = "https://evil.example/track?sig=planted-signature"
    answer = {
        "items": [
            _item("found", 0.85, _track("Sure Song", duration=241.4, url=planted_url)),
            _item("found", 0.84, _track("Loose Song", duration=200)),  # the gateway's 0.78 cut
            _item("unsure", 0.99, _track("Unsure Song", duration=180)),
            _item("found", 0.95, None),
            _item("missing", 0.0, _track("Ignored")),
            _item("found", 0.9, _track("Zero Length", duration=0)),
            # A reference outside the pattern makes the track unusable: missing (SR C2).
            _item("found", 0.99, _track("Bad Reference", duration=200, reference="../planted")),
        ],
        "selected": [_track("Sure Song")],
        "playlist_name": "planted-playlist",
        "found": 3,
    }
    catalogue.inject("body", MATCH_PATH, body=json.dumps(answer).encode())
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    job_id = await _run(env)
    assert (_job(admin, job_id).state, _job(admin, job_id).error_category) == ("completed", None)
    [sent] = calls(catalogue, MATCH_PATH)
    assert [t["title"] for t in sent.body["tracks"]] == [
        "Sure Song", "Loose Song", "Unsure Song", "No Track Song", "Gone Song", "Zero Length",
        "Bad Reference",
    ]  # fmt: skip
    content = _match(admin, job_id).content
    assert [s["status"] for s in content["songs"]] == [
        "found", "unsure", "unsure", "missing", "missing", "found", "missing", "missing",
        "missing",
    ]  # fmt: skip
    # The reference and SwedeBeat's BPM are kept (ADR 0036 section 3.4); every other field not.
    assert content["songs"][0]["swedebeat"] == {
        "title": "Sure Song", "artist": ARTIST, "duration": 241,
        "reference": "planted-ref-Kx1", "bpm": 128.0,
    }  # fmt: skip
    assert content["songs"][5]["swedebeat"]["duration"] is None  # 0 s is no length
    assert content["swedebeat_sec"] == 241
    blob = json.dumps(content)
    for planted in ("../planted", "evil.example", "planted-playlist", "selected"):
        assert planted not in blob


async def test_all_missing_is_asked_once_more_after_five_seconds(
    env: Env,
    admin: Connection,
    make_actor: Any,
    catalogue: FakeGateway,
    configured: None,
    paused: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    rider = await _one(env, make_actor, "outage", 400)
    catalogue.inject("all_missing", MATCH_PATH)
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    job_id = await _run(env)
    assert paused == [5.0]
    assert len(calls(catalogue, MATCH_PATH)) == 2
    content = _match(admin, job_id).content
    assert [s["status"] for s in content["songs"]] == ["found", "unsure", "missing", "found"]
    assert "swedebeat match all missing" not in caplog.text
    # Still all missing the second time: stored as it is, with the operator line (counts only).
    catalogue.requests.clear()
    other = await _one(env, make_actor, "empty", 410)
    catalogue.inject("all_missing", MATCH_PATH)
    catalogue.inject("all_missing", MATCH_PATH)
    assert (await _post_job(other.actor, _body(other))).status_code == 201
    second = await _run(env)
    assert len(calls(catalogue, MATCH_PATH)) == 2
    assert _job(admin, second).state == "completed"
    assert _match(admin, second).content["missing"] == 4
    [line] = [r for r in caplog.records if r.getMessage() == "swedebeat match all missing"]
    assert getattr(line, "sent", None) == 3
    assert FOUND_TITLE not in caplog.text and other.bearer not in caplog.text  # noqa: PT018


async def test_a_short_all_missing_list_is_not_asked_again(
    env: Env,
    admin: Connection,
    make_actor: Any,
    catalogue: FakeGateway,
    configured: None,
    paused: list[float],
) -> None:
    songs = [_song(501, "Nope One", "X", 120.0), _song(502, "Nope Two", "Y", 120.0)]
    rider = await _one(env, make_actor, "short", 500, songs=songs)
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    job_id = await _run(env)
    assert _job(admin, job_id).state == "completed"
    assert len(calls(catalogue, MATCH_PATH)) == 1
    assert paused == []


async def test_a_list_with_nothing_sendable_sends_nothing(
    env: Env, admin: Connection, make_actor: Any, catalogue: FakeGateway, configured: None
) -> None:
    songs = [_song(601, "", ARTIST, 120.0), _song(602, "Title", "  ", 120.0)]
    rider = await _one(env, make_actor, "empty-list", 600, songs=songs)
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    job_id = await _run(env)
    assert _job(admin, job_id).state == "completed"
    assert _match(admin, job_id).content["missing"] == 2
    assert calls(catalogue, MATCH_PATH) == []


# ADR 0035 section 6's table, the rows that end the job without the 401 confirmation: what the
# gateway answers, the job's state and category, and whether the connection records it.
FAILURES = [
    ("unknown_client", {}, "failed", "app_misconfigured", False),
    ("status", {"status": 403}, "failed", "app_misconfigured", False),
    ("status", {"status": 502}, "queued", "upstream_unavailable", True),
    ("status", {"status": 500}, "queued", "upstream_unavailable", True),
    ("status", {"status": 413}, "failed", "invalid_response", True),
    ("status", {"status": 422}, "failed", "invalid_response", True),
    ("status", {"status": 404}, "failed", "invalid_response", True),
    ("body", {"body": b"not json"}, "failed", "invalid_response", True),
    ("wrong_count", {}, "failed", "invalid_response", True),
    ("gzip", {}, "failed", "invalid_response", True),
    (
        "redirect",
        {"status": 302, "location": "/catalog/other"},
        "failed",
        "outbound_blocked",
        False,
    ),
]


@pytest.mark.parametrize(("kind", "params", "state", "category", "recorded"), FAILURES)
async def test_each_failed_match_has_its_job_category(
    env: Env,
    admin: Connection,
    make_actor: Any,
    catalogue: FakeGateway,
    configured: None,
    caplog: pytest.LogCaptureFixture,
    kind: str,
    params: dict[str, Any],
    state: str,
    category: str,
    recorded: bool,
) -> None:
    caplog.set_level(logging.INFO)
    rider = await _one(env, make_actor, "fails", 700)
    before = _held(admin, rider)
    catalogue.inject(kind, MATCH_PATH, **params)
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    job_id = await _run(env)
    row = _job(admin, job_id)
    if state == "failed":
        assert (row.state, row.error_category) == ("failed", category)
    else:
        assert (row.state, row.error_category) == ("queued", None)
        assert category in _event_categories(admin, job_id)
    assert len(calls(catalogue, MATCH_PATH)) == 1
    assert calls(catalogue, SESSION_PATH) == []  # only a refused bearer is confirmed
    assert _stored(admin, job_id) == 0
    held = _held(admin, rider)
    assert held.status == "connected"  # none of these flips the connection
    assert held.last_error == (category if recorded else before.last_error)
    if category == "app_misconfigured":  # the row is not touched at all
        assert held.last_used_at == before.last_used_at
        lines = [
            r
            for r in caplog.records
            if r.getMessage() == "swedebeat gateway refused the client key"
        ]
        assert [r.levelno for r in lines] == [logging.ERROR]
        shown = (await rider.actor.get(f"/api/jobs/{job_id}")).json()
        assert shown["error_message"] == "Swedebeat is not available on this server right now."
    assert rider.bearer not in caplog.text and CLIENT_KEY not in caplog.text  # noqa: PT018


# The §5 confirmation: what the session check answers after a refused bearer, then the job's
# state and category, the connection's status and its last_error.
CONFIRMATIONS = [
    (None, {}, "queued", "upstream_unavailable", "connected", None),
    ("bearer_401", {}, "failed", "needs_reconnect", "needs_reconnect", "needs_reconnect"),
    ("status", {"status": 502}, "queued", "upstream_unavailable", "connected", None),
    ("status", {"status": 503}, "queued", "upstream_unavailable", "connected", None),
    ("unknown_client", {}, "failed", "app_misconfigured", "connected", None),
    ("status", {"status": 403}, "failed", "app_misconfigured", "connected", None),
    ("rate_limited", {"retry_after": "120"}, "queued", "rate_limited", "connected", None),
    ("body", {"body": b'{"valid": false}'}, "failed", "invalid_response", "connected", None),
]


@pytest.mark.parametrize(
    ("kind", "params", "state", "category", "status", "last_error"), CONFIRMATIONS
)
async def test_a_refused_bearer_is_confirmed_once_before_anything_is_decided(
    env: Env,
    admin: Connection,
    make_actor: Any,
    catalogue: FakeGateway,
    configured: None,
    paused: list[float],
    kind: str | None,
    params: dict[str, Any],
    state: str,
    category: str,
    status: str,
    last_error: str | None,
) -> None:
    rider = await _one(env, make_actor, "confirm", 800)
    catalogue.inject("bearer_401", MATCH_PATH)
    if kind is not None:
        catalogue.inject(kind, SESSION_PATH, **params)
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    job_id = await _run(env)
    assert paused == [5.0]
    [check] = calls(catalogue, SESSION_PATH)
    assert check.method == "GET"
    assert check.body is None
    assert check.headers["authorization"] == f"Bearer {rider.bearer}"
    assert check.headers["x-api-key"] == CLIENT_KEY
    assert len(calls(catalogue, MATCH_PATH)) == 1
    row = _job(admin, job_id)
    if state == "failed":
        assert (row.state, row.error_category) == ("failed", category)
    else:
        assert (row.state, row.error_category) == ("queued", None)
        assert category in _event_categories(admin, job_id)
    held = _held(admin, rider)
    assert (held.status, held.last_error) == (status, last_error)
    # ``bearer_refused`` is the client's word only: never a job's or a connection's category.
    assert "bearer_refused" not in _event_categories(admin, job_id)
    assert "bearer_refused" not in json.dumps(row._asdict(), default=str)
    if kind is None:
        # The check passed: SwedeBeat's refusal may be cached for 300 s, so it waits past that.
        assert 300 <= _waits(admin, job_id) <= 330
    if kind == "rate_limited":
        assert row.spotify_retry_after == 120


def test_bearer_refused_is_in_no_category_list() -> None:
    assert "bearer_refused" not in JOB_CATEGORIES
    assert "bearer_refused" not in CONNECTION_CATEGORIES


_WAIT = "Swedebeat asks to slow down; trying again in about 2 minutes."
_STOPPED = "Swedebeat asks to slow down. This match has stopped."
_HOUR = "Swedebeat asks to slow down; trying again in about 60 minutes."


async def test_a_rate_limit_pauses_the_job_and_names_the_wait(
    env: Env, admin: Connection, make_actor: Any, catalogue: FakeGateway, configured: None
) -> None:
    rider = await _one(env, make_actor, "rate", 21)
    catalogue.inject("rate_limited", MATCH_PATH, retry_after="90")
    created = await _post_job(rider.actor, _body(rider))
    assert created.status_code == 201, created.text
    job_id = await _run(env)
    row = _job(admin, job_id)
    assert row.state == "queued"
    assert (row.spotify_failure, row.spotify_retry_after, row.spotify_rate_limits) == (
        "rate_limited",
        90,
        0,
    )
    assert 80 <= _waits(admin, job_id) <= 90
    assert "rate_limited" in _event_categories(admin, job_id)
    assert len(calls(catalogue, MATCH_PATH)) == 1
    shown = await rider.actor.get(f"/api/jobs/{job_id}")
    assert shown.status_code == 200, shown.text
    view = shown.json()
    assert (view["state"], view["error_category"], view["error_message"]) == (
        "queued",
        None,
        _WAIT,
    )
    assert rider.bearer not in shown.text
    assert "slow down" not in json.dumps(row._asdict(), default=str)  # the fake's own words
    assert "Spotify" not in view["error_message"]
    # A 5xx is upstream_unavailable: retried, with no Spotify note.
    other = await _one(env, make_actor, "down", 31)
    catalogue.inject("status", MATCH_PATH, status=503)
    assert (await _post_job(other.actor, _body(other))).status_code == 201
    failed = await _run(env)
    down_row = _job(admin, failed)
    assert (down_row.state, down_row.spotify_failure) == ("queued", None)
    assert "upstream_unavailable" in _event_categories(admin, failed)
    assert (await other.actor.get(f"/api/jobs/{failed}")).json()["error_message"] is None


async def test_a_long_retry_after_is_named_as_the_one_hour_pause(
    env: Env, admin: Connection, make_actor: Any, catalogue: FakeGateway, configured: None
) -> None:
    """A Retry-After of two hours is stored, and the pause and the sentence both stop at an hour."""
    rider = await _one(env, make_actor, "long", 61)
    catalogue.inject("rate_limited", MATCH_PATH, retry_after="7200")
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    job_id = await _run(env)
    row = _job(admin, job_id)
    assert row.state == "queued"
    assert row.spotify_retry_after == 7200
    assert 3500 <= _waits(admin, job_id) <= 3600
    message = (await rider.actor.get(f"/api/jobs/{job_id}")).json()["error_message"]
    assert message == _HOUR
    assert "7200" not in message and "Spotify" not in message  # noqa: PT018


async def test_three_rate_limits_name_the_wait_and_are_not_a_spotify_quota(
    env: Env, admin: Connection, make_actor: Any, catalogue: FakeGateway, configured: None
) -> None:
    """Three 429s end ``rate_limited``, not Spotify's ``quota_exhausted``; the sentence says the
    match has stopped."""
    rider = await _one(env, make_actor, "again", 51)
    job_id: UUID | None = None
    states: list[str] = []
    for _ in range(3):
        catalogue.inject("rate_limited", MATCH_PATH, retry_after="90")
        if job_id is not None:
            admin.execute(
                text("UPDATE jobs SET available_at = now() WHERE id = :id"), {"id": job_id}
            )
        job_id = await _run(env) if job_id is not None else await _created_and_run(env, rider)
        row = _job(admin, job_id)
        states.append(row.state)
        assert row.spotify_rate_limits == 0
        assert row.error_category != "quota_exhausted"
    assert job_id is not None
    assert states == ["queued", "queued", "failed"]
    finished = _job(admin, job_id)
    assert (finished.error_category, finished.spotify_failure, finished.spotify_retry_after) == (
        "rate_limited",
        "rate_limited",
        90,
    )
    message = (await rider.actor.get(f"/api/jobs/{job_id}")).json()["error_message"]
    assert message == _STOPPED


async def _created_and_run(env: Env, rider: Rider) -> UUID:
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    return await _run(env)


# A row past its lifetime, and one with none (no gateway-era row lacks one): both are not live.
_EXPIRED = ("now() - interval '1 second'", "NULL")


def _expire(admin: Connection, rider: Rider, expires_at: str = _EXPIRED[0]) -> None:
    assert expires_at in _EXPIRED
    admin.execute(
        text(
            f"UPDATE connections SET expires_at = {expires_at} "  # noqa: S608 - a fixed literal
            "WHERE owner_id = :o AND provider = 'swedebeat'"
        ),
        {"o": rider.user_id},
    )


@pytest.mark.parametrize("expires_at", _EXPIRED)
async def test_a_bearer_past_or_without_expires_at_is_refused_at_admission_and_in_prepare(
    env: Env,
    admin: Connection,
    make_actor: Any,
    catalogue: FakeGateway,
    configured: None,
    expires_at: str,
) -> None:
    rider = await _one(env, make_actor, "expiry", 900)
    # Admitted while live, expired before the worker runs it: no request, no flip.
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    _expire(admin, rider, expires_at)
    job_id = await _run(env)
    assert (_job(admin, job_id).state, _job(admin, job_id).error_category) == (
        "failed", "not_connected",
    )  # fmt: skip
    # Past it already: never queued.
    refused = await _post_job(rider.actor, _body(rider))
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "not_connected")
    assert calls(catalogue, MATCH_PATH) == []
    assert calls(catalogue, SESSION_PATH) == []
    assert _held(admin, rider).status == "connected"


@pytest.mark.parametrize(
    "missing", [{"swedebeat_gateway_key": None}, {"swedebeat_gateway_url": None}]
)
async def test_a_worker_without_the_gateway_ends_app_misconfigured_without_a_request(
    env: Env,
    admin: Connection,
    make_actor: Any,
    catalogue: FakeGateway,
    configured: None,
    missing: dict[str, Any],
) -> None:
    rider = await _one(env, make_actor, "no-gateway", 950)
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    swedebeat_match.configure(match_config(env, **missing))
    job_id = await _run(env)
    row = _job(admin, job_id)
    assert (row.state, row.error_category) == ("failed", "app_misconfigured")
    assert calls(catalogue, MATCH_PATH) == []
    held = _held(admin, rider)
    assert (held.status, held.last_error) == ("connected", None)


def _flag(admin: Connection, value: str | None) -> None:
    admin.execute(text("DELETE FROM app_settings WHERE key = 'swedebeat_enabled'"))
    if value is not None:
        admin.execute(
            text(
                "INSERT INTO app_settings (key, value) VALUES "
                "('swedebeat_enabled', CAST(:v AS jsonb))"
            ),
            {"v": json.dumps(value)},
        )


async def test_the_flag_off_admits_nothing_and_a_queued_job_calls_nothing(
    env: Env, admin: Connection, make_actor: Any, catalogue: FakeGateway, configured: None
) -> None:
    rider = await _one(env, make_actor, "flag", 51)
    created = await _post_job(rider.actor, _body(rider))
    assert created.status_code == 201, created.text
    _flag(admin, "off")
    try:
        job_id = await _run(env)
        row = _job(admin, job_id)
        assert (row.state, row.error_category) == ("failed", "feature_off")
        refused = await _post_job(rider.actor, _body(rider))
        assert refused.status_code == 409
        assert refused.json()["detail"]["reason"] == "feature_off"
        assert calls(catalogue, MATCH_PATH) == []
    finally:
        _flag(admin, None)


def _rows(admin: Connection, table: str, owner: UUID) -> int:
    return admin.execute(
        text(f"SELECT count(*) FROM {table} WHERE owner_id = :o"),  # noqa: S608 - fixed names
        {"o": owner},
    ).scalar_one()


async def test_the_preference_off_admits_no_match_and_sends_nothing(
    env: Env, admin: Connection, make_actor: Any, catalogue: FakeGateway, configured: None
) -> None:
    """#525: a stored, connected Swedebeat row is not enough; the user's own switch is."""
    rider = await _one(env, make_actor, "pref-off", 61)
    await use_swedebeat(env.factory, rider.user_id, on=False)
    refused = await _post_job(rider.actor, _body(rider))
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "feature_off"
    assert calls(catalogue, MATCH_PATH) == []
    assert _rows(admin, "jobs", rider.user_id) == 0
    assert _rows(admin, "credit_ledger", rider.user_id) == 0
    # The same user with the switch on again is admitted: the refusal was the preference's.
    await use_swedebeat(env.factory, rider.user_id)
    admitted = await _post_job(rider.actor, _body(rider))
    assert admitted.status_code == 201, admitted.text


async def test_a_user_connected_before_the_preference_existed_is_refused(
    env: Env, admin: Connection, make_actor: Any, catalogue: FakeGateway, configured: None
) -> None:
    """A connection made before migration 0028 has no ``user_preferences`` row: that is off."""
    rider = await _one(env, make_actor, "pre-0028", 71)
    admin.execute(text("DELETE FROM user_preferences WHERE owner_id = :o"), {"o": rider.user_id})
    assert _rows(admin, "connections", rider.user_id) == 1
    refused = await _post_job(rider.actor, _body(rider))
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "feature_off"
    assert calls(catalogue, MATCH_PATH) == []
    assert _rows(admin, "jobs", rider.user_id) == 0
    assert _rows(admin, "credit_ledger", rider.user_id) == 0


async def test_a_queued_match_whose_user_turned_the_preference_off_calls_nothing(
    env: Env, admin: Connection, make_actor: Any, catalogue: FakeGateway, configured: None
) -> None:
    """#525: admitted with the switch on, run after the user turned it off: ``prepare`` re-reads
    it and the job ends ``feature_off`` before any gateway request."""
    rider = await _one(env, make_actor, "pref-queued", 81)
    created = await _post_job(rider.actor, _body(rider))
    assert created.status_code == 201, created.text
    await use_swedebeat(env.factory, rider.user_id, on=False)
    job_id = await _run(env)
    row = _job(admin, job_id)
    assert (row.state, row.error_category) == ("failed", "feature_off")
    assert calls(catalogue, MATCH_PATH) == []


def _soundless(admin: Connection, class_id: UUID, value: Any) -> None:
    admin.execute(
        text("UPDATE classes SET music_source = CAST(:m AS jsonb) WHERE id = :c"),
        {"m": None if value is None else json.dumps(value), "c": class_id},
    )


async def test_a_soundless_class_is_not_matched_at_admit_or_in_the_store(
    env: Env, admin: Connection, make_actor: Any, catalogue: FakeGateway, configured: None
) -> None:
    """ADR 0007 amendment of 2026-11-03, item 9 (#530): the class's ``music_source`` is read
    explicitly, so even a class that still holds a song list is refused when it is soundless."""
    rider = await _one(env, make_actor, "soundless", 91)
    _soundless(admin, rider.class_id, {"source": "none"})
    refused = await _post_job(rider.actor, _body(rider))
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "invalid_input")
    assert calls(catalogue, MATCH_PATH) == []
    assert _rows(admin, "jobs", rider.user_id) == 0
    # Admitted while it has no source; soundless by the store: nothing is stored.
    _soundless(admin, rider.class_id, None)
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    _soundless(admin, rider.class_id, {"source": "none"})
    job_id = await _run(env)
    row = _job(admin, job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert calls(catalogue, MATCH_PATH) == []  # refused in prepare, before any lookup (#470)
    assert _stored(admin, job_id) == 0


async def test_a_studio_class_is_not_matched_at_admit_or_in_the_store(
    env: Env, admin: Connection, make_actor: Any, catalogue: FakeGateway, configured: None
) -> None:
    """ADR 0036 section 2: a studio class's music kind never changes, so no other kind's match."""
    rider = await _one(env, make_actor, "studioclass", 92)
    _soundless(admin, rider.class_id, {"source": "swedebeat_studio"})
    refused = await _post_job(rider.actor, _body(rider))
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "invalid_input")
    assert _rows(admin, "jobs", rider.user_id) == 0
    _soundless(admin, rider.class_id, None)
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    _soundless(admin, rider.class_id, {"source": "swedebeat_studio"})
    job_id = await _run(env)
    assert (_job(admin, job_id).state, _job(admin, job_id).error_category) == (
        "failed",
        "invalid_input",
    )
    assert calls(catalogue, MATCH_PATH) == []
    assert _stored(admin, job_id) == 0


@pytest.mark.parametrize("turns_soundless", [True, False])
async def test_a_class_that_turns_soundless_while_matching_is_not_stored(
    env: Env,
    admin: Connection,
    make_actor: Any,
    catalogue: FakeGateway,
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
    turns_soundless: bool,
) -> None:
    """Past the prepare check (#470) the store's ``check_stored`` still refuses: the lookup ran,
    nothing is stored. The control stores the match."""
    rider = await _one(env, make_actor, f"soundless-late-{turns_soundless}", 91)
    _soundless(admin, rider.class_id, None)
    real_match = match_kind.match_songs

    async def match_then_flip(*args: Any, **kwargs: Any) -> Any:
        matched = await real_match(*args, **kwargs)
        if turns_soundless:
            _soundless(admin, rider.class_id, {"source": "none"})
        return matched

    monkeypatch.setattr(match_kind, "match_songs", match_then_flip)
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    job_id = await _run(env)
    row = _job(admin, job_id)
    assert calls(catalogue, MATCH_PATH) != []
    if turns_soundless:
        assert (row.state, row.error_category) == ("failed", "invalid_input")
        assert _stored(admin, job_id) == 0
    else:
        assert (row.state, row.error_category) == ("completed", None)
        assert _stored(admin, job_id) == 1
