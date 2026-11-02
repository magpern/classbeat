"""``swedebeat_match`` on the fake catalogue (ADR 0016 §5 and §8, B1).

A real ``song_list``, a real Swedebeat connection (the connect fake issues the token; the job
opens it through ``UserContext``) and the catalogue client pointed at ``CatalogueFake``. No
network, no live account. Two users' tokens never appear on each other's requests, and a request
carries only a title and an artist.
"""

import asyncio
import json
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import ORIGIN, Env, running_env, session_cookie
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
from classbeat import auth, connections, swedebeat_match
from classbeat.db import user_session
from classbeat.egress import swedebeat_cloud_test_client
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.songs.song_list import (
    Song,
    SongListContent,
    SongListMusic,
    content_hash,
    fingerprint,
)
from classbeat.swedebeat_match import kind as match_kind
from classbeat.swedebeat_match.config import MatchConfig
from classbeat.swedebeat_match.kind import SWEDEBEAT_MATCH
from ctx import make_context
from jobtools import engine_for
from mfatools import Actor
from swedebeatfake import EMAIL, EMAIL_B, PASSWORD, PASSWORD_B

pytestmark = pytest.mark.usefixtures("swedebeat_api")


@pytest.fixture
async def env(
    clean_auth: None, app_database_url: str, swedebeat_app: dict[str, str]
) -> AsyncIterator[Env]:
    from pydantic import SecretStr

    async with running_env(
        app_database_url, swedebeat_enabled=True, swedebeat_app=SecretStr(json.dumps(swedebeat_app))
    ) as running:
        yield running


@pytest.fixture
def admin(database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


@pytest.fixture
def catalogue(monkeypatch: pytest.MonkeyPatch) -> CatalogueFake:
    fake = CatalogueFake()
    monkeypatch.setattr(match_kind, "catalogue_client", lambda: swedebeat_cloud_test_client(fake))
    return fake


@pytest.fixture
def configured(env: Env) -> Iterator[None]:
    swedebeat_match.configure(
        MatchConfig(enabled=True, keyring=env.app.state.keyring, pace_seconds=0)
    )
    yield
    swedebeat_match.configure(MatchConfig.from_settings(env.settings))


def _spotify_id(n: int) -> str:
    text_id = f"Sp0tifyNeverSent{n:06d}"
    assert len(text_id) == 22
    return text_id


def _songs(start: int, bpm: float) -> list[Song]:
    def one(n: int, title: str, artist: str) -> Song:
        return Song(
            spotify_id=_spotify_id(n),
            title=title,
            artist=artist,
            role="climb",
            bpm=bpm,
            length_sec=240,
            new=False,
        )

    return [
        one(start, FOUND_TITLE, ARTIST),
        one(start + 1, UNSURE_TITLE, ARTIST),
        one(start + 2, "Not In The Catalogue", "Nobody"),
        one(start + 3, FOUND_TITLE, ARTIST),
    ]


@dataclass
class Rider:
    user_id: UUID
    actor: Actor
    connection_id: UUID
    token: str
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
        "/api/connections/swedebeat", {"email": email, "password": password}
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
) -> Rider:
    user_id = await env.user(account)
    actor = await make_actor(user_id)
    connection_id = await _connect(actor, swedebeat_email, password)
    songs = _songs(start, bpm)
    class_id, revision_id, digest = await _plant(env, user_id, class_title, songs)
    opened = await connections.open_connection(
        env.factory, env.app.state.keyring, make_context(user_id), "swedebeat"
    )
    token = opened.credentials.swedebeat_token
    assert isinstance(token, str)
    assert token
    return Rider(
        user_id,
        actor,
        connection_id,
        token,
        class_id,
        revision_id,
        digest,
        [s.spotify_id for s in songs],
        bpm,
        class_title,
    )


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


def _urls_carry_titles_only(calls: list[Any], rider: Rider) -> None:
    forbidden = [
        *rider.spotify_ids,
        str(rider.bpm),
        rider.class_title,
        str(rider.class_id),
        str(rider.user_id),
        PLANTED_REFERENCE,
        PLANTED_URL,
        PLANTED_KEY,
        "spotify",
    ]
    assert calls
    for call in calls:
        text_url = unquote(call.url).lower()
        assert (
            FOUND_TITLE.lower() in text_url
            or UNSURE_TITLE.lower() in text_url
            or "not in the catalogue" in text_url
        )
        assert ARTIST.lower() in text_url or "nobody" in text_url
        for secret in forbidden:
            assert str(secret).lower() not in text_url
        assert set(call.headers) <= {"accept", "user-agent", "x-auth-token", "host"}
        assert call.token == rider.token


async def test_two_users_match_on_the_fake_and_their_tokens_never_mix(
    env: Env,
    admin: Connection,
    make_actor: Any,
    catalogue: CatalogueFake,
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(match_kind, "_pause", record)
    swedebeat_match.configure(
        MatchConfig(enabled=True, keyring=env.app.state.keyring, pace_seconds=0.2)
    )
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
    assert alice.token != bob.token
    for rider in (alice, bob):
        created = await _post_job(rider.actor, _body(rider))
        assert created.status_code == 201, created.text
    alice_job, bob_job = await _run(env), await _run(env)
    assert slept == [0.2, 0.2, 0.2, 0.2]
    assert [call.token for call in catalogue.calls] == [alice.token] * 3 + [bob.token] * 3
    _urls_carry_titles_only(catalogue.calls[:3], alice)
    _urls_carry_titles_only(catalogue.calls[3:], bob)
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
        assert content["swedebeat_sec"] == FOUND_SECONDS * 2
        assert content["songs"][0]["swedebeat"]["duration"] == FOUND_SECONDS
        assert content["songs"][1]["swedebeat"]["duration"] == UNSURE_SECONDS
        assert content["songs"][2]["swedebeat"] is None
        assert content["swedebeat_sec"] != FOUND_SECONDS * 2 + UNSURE_SECONDS
        blob = json.dumps(content)
        assert PLANTED_REFERENCE not in blob
        assert PLANTED_KEY not in blob
        assert "evil.example" not in blob
        for spotify_id in rider.spotify_ids:
            assert spotify_id not in blob
        assert match.music == {
            "source": "swedebeat_catalogue",
            "song_list_hash": rider.list_hash.hex(),
        }
        assert match.provenance["origin"] == "swedebeat_match"
        assert match.parent_id == rider.revision_id
        held = admin.execute(
            text(
                "SELECT status, last_error FROM connections "
                "WHERE owner_id = :o AND provider = 'swedebeat'"
            ),
            {"o": rider.user_id},
        ).one()
        assert (held.status, held.last_error) == ("connected", None)


_WAIT = "Swedebeat asks to slow down; trying again in about 2 minutes."
_PLANTED_BODY = b'{"error":"catalogue-body-planted-9f3a"}'


async def test_a_rate_limit_pauses_the_job_and_a_5xx_is_upstream_unavailable(
    env: Env, admin: Connection, make_actor: Any, catalogue: CatalogueFake, configured: None
) -> None:
    rider = await _rider(
        env,
        make_actor,
        account="rate@example.com",
        swedebeat_email=EMAIL,
        password=PASSWORD,
        start=21,
        bpm=100.0,
        class_title="Rate Brief",
    )
    catalogue.status = 429
    catalogue.retry_after = "90"
    catalogue.body = _PLANTED_BODY
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
    remaining = float(
        admin.execute(
            text("SELECT extract(epoch FROM (available_at - now())) FROM jobs WHERE id = :id"),
            {"id": job_id},
        ).scalar_one()
    )
    assert 80 <= remaining <= 90
    kinds = [
        event.payload.get("category")
        for event in admin.execute(
            text("SELECT payload FROM job_events WHERE job_id = :id"), {"id": job_id}
        )
        if isinstance(event.payload, dict)
    ]
    assert "rate_limited" in kinds
    assert len(catalogue.calls) == 1
    assert catalogue.calls[0].token == rider.token
    shown = await rider.actor.get(f"/api/jobs/{job_id}")
    assert shown.status_code == 200, shown.text
    view = shown.json()
    assert (view["state"], view["error_category"], view["error_message"]) == (
        "queued",
        None,
        _WAIT,
    )
    assert rider.token not in shown.text
    assert "catalogue-body-planted-9f3a" not in shown.text
    assert "Spotify" not in view["error_message"]

    catalogue.calls.clear()
    catalogue.body = None
    catalogue.status = 503
    catalogue.retry_after = None
    other = await _rider(
        env,
        make_actor,
        account="down@example.com",
        swedebeat_email=EMAIL_B,
        password=PASSWORD_B,
        start=31,
        bpm=101.0,
        class_title="Down Brief",
    )
    assert (await _post_job(other.actor, _body(other))).status_code == 201
    failed = await _run(env)
    assert _job(admin, failed).state == "queued"
    again = [
        event.payload.get("category")
        for event in admin.execute(
            text("SELECT payload FROM job_events WHERE job_id = :id"), {"id": failed}
        )
        if isinstance(event.payload, dict)
    ]
    assert "upstream_unavailable" in again
    assert len(catalogue.calls) == 1
    down_row = _job(admin, failed)
    assert down_row.spotify_failure is None
    down = await other.actor.get(f"/api/jobs/{failed}")
    assert down.status_code == 200, down.text
    assert down.json()["error_message"] is None


async def test_three_catalogue_rate_limits_name_the_wait_and_are_not_a_spotify_quota(
    env: Env, admin: Connection, make_actor: Any, catalogue: CatalogueFake, configured: None
) -> None:
    """Three Swedebeat 429s end ``rate_limited``, not Spotify's ``quota_exhausted``."""
    rider = await _rider(
        env,
        make_actor,
        account="again@example.com",
        swedebeat_email=EMAIL,
        password=PASSWORD,
        start=51,
        bpm=103.0,
        class_title="Again Brief",
    )
    catalogue.status = 429
    catalogue.retry_after = "90"
    catalogue.body = _PLANTED_BODY
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    job_id: UUID | None = None
    states: list[str] = []
    for _ in range(3):
        if job_id is not None:
            admin.execute(
                text("UPDATE jobs SET available_at = now() WHERE id = :id"), {"id": job_id}
            )
        job_id = await _run(env)
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
    shown = await rider.actor.get(f"/api/jobs/{job_id}")
    assert shown.status_code == 200, shown.text
    message = shown.json()["error_message"]
    assert message == _WAIT
    assert rider.token not in shown.text
    assert "catalogue-body-planted-9f3a" not in shown.text
    assert "Spotify" not in message


async def test_a_401_reconnects_and_sends_no_further_song(
    env: Env, admin: Connection, make_actor: Any, catalogue: CatalogueFake, configured: None
) -> None:
    rider = await _rider(
        env,
        make_actor,
        account="expired@example.com",
        swedebeat_email=EMAIL,
        password=PASSWORD,
        start=41,
        bpm=102.0,
        class_title="Expired Brief",
    )
    catalogue.status = 401
    assert (await _post_job(rider.actor, _body(rider))).status_code == 201
    job_id = await _run(env)
    row = _job(admin, job_id)
    assert (row.state, row.error_category) == ("failed", "needs_reconnect")
    assert len(catalogue.calls) == 1
    assert catalogue.calls[0].token == rider.token
    held = admin.execute(
        text("SELECT status FROM connections WHERE owner_id = :o AND provider = 'swedebeat'"),
        {"o": rider.user_id},
    ).scalar_one()
    assert held == "needs_reconnect"
    assert (
        admin.execute(
            text("SELECT count(*) FROM class_revisions WHERE job_id = :id"), {"id": job_id}
        ).scalar_one()
        == 0
    )


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
    env: Env, admin: Connection, make_actor: Any, catalogue: CatalogueFake, configured: None
) -> None:
    rider = await _rider(
        env,
        make_actor,
        account="flag@example.com",
        swedebeat_email=EMAIL,
        password=PASSWORD,
        start=51,
        bpm=103.0,
        class_title="Flag Brief",
    )
    created = await _post_job(rider.actor, _body(rider))
    assert created.status_code == 201, created.text
    _flag(admin, "off")
    try:
        job_id = await _run(env)
        row = _job(admin, job_id)
        assert (row.state, row.error_category) == ("failed", "feature_off")
        assert catalogue.calls == []
        refused = await _post_job(rider.actor, _body(rider))
        assert refused.status_code == 409
        assert refused.json()["detail"]["reason"] == "feature_off"
        assert catalogue.calls == []
    finally:
        _flag(admin, None)
