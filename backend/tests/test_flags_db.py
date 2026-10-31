"""The flags against the database (ADR 0011 sections 7 and 9; FL1, #216): the sign-up mode on
the password and OAuth paths, ``GET /api/settings/public`` and the song-picking admit. The rows
are written as the superuser, as ``classbeat_maint`` would. Fakes only."""

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text

from authtools import Env, running_env
from classbeat import auth, songs
from flows import PASSWORD, Browser, reason
from jobtools import engine_for
from oauthtools import CLIENT_IDS, location, sign_in_with, start
from oidcfake import FakeProviders
from revisesongtools import change, picked
from smtpfake import SmtpFake

PUBLIC = "/api/settings/public"


@contextmanager
def stored(database_url: str, **rows: Any) -> Iterator[None]:
    """``app_settings`` rows for a test, none before or after."""
    with engine_for(database_url, isolation_level="AUTOCOMMIT") as engine, engine.connect() as c:
        c.execute(text("DELETE FROM app_settings"))
        for key, value in rows.items():
            c.execute(
                text("INSERT INTO app_settings (key, value) VALUES (:k, CAST(:v AS jsonb))"),
                {"k": key, "v": json.dumps(value)},
            )
        try:
            yield
        finally:
            c.execute(text("DELETE FROM app_settings"))


async def sign_up(env: Env, code: str | None) -> Any:
    body = {"email": "new.user@example.com", "password": PASSWORD, "invite_code": code}
    return await Browser(env).post("/api/auth/signup", body)


async def invite(env: Env) -> str:
    async with env.db() as db:
        return await auth.create_invite(db, timedelta(days=1))


# --- GET /api/settings/public -------------------------------------------------------------------


async def test_public_settings_hold_the_banner_and_the_effective_mode_and_nothing_else(
    clean_auth: None, app_database_url: str, database_url: str
) -> None:
    banner = {"text": "Maintenance <b>tonight</b>", "level": "warning"}
    async with running_env(app_database_url, signup_mode="open") as env:
        with stored(database_url):
            response = await Browser(env).get(PUBLIC)  # no session, no stored row
            assert response.status_code == 200
            assert response.json() == {"banner": None, "signup_mode": "open"}
            assert response.headers["cache-control"] == "no-store"
        with stored(database_url, banner=banner, signup_mode="invite", trial={"plan": "pro"}):
            response = await Browser(env).get(PUBLIC)
            assert response.json() == {"banner": banner, "signup_mode": "invite"}
            assert set(response.json()) == {"banner", "signup_mode"}  # no other setting leaks


async def test_an_invalid_or_oversized_banner_is_not_served(
    clean_auth: None, app_database_url: str, database_url: str
) -> None:
    async with running_env(app_database_url) as env:
        for bad in (
            {"text": "x" * 281, "level": "info"},
            {"text": "Hi", "level": "danger"},
            {"text": 5, "level": "info"},
            "just a string",
            # Not plain text (#227): stored past the write side, never served.
            {"text": "Back at \u202e9 ta kcab", "level": "info"},
            {"text": "Back at\u200b 9", "level": "info"},
            {"text": "Back at\u2028 9", "level": "warning"},
            {"text": "Back at\x85 9", "level": "info"},
        ):
            with stored(database_url, banner=bad):
                response = await Browser(env).get(PUBLIC)
                assert response.json()["banner"] is None, bad
                assert "Back at" not in response.text


@pytest.mark.parametrize(
    ("environment", "database", "effective"),
    [
        ("open", None, "open"),
        ("open", "invite", "invite"),
        ("open", "closed", "closed"),
        ("invite", "closed", "closed"),
        ("closed", "invite", "closed"),
        ("invite", "open", "invite"),  # the database cannot say open
    ],
)
async def test_the_public_mode_is_the_stricter_of_the_environment_and_the_database(
    clean_auth: None,
    app_database_url: str,
    database_url: str,
    environment: str,
    database: str | None,
    effective: str,
) -> None:
    rows = {} if database is None else {"signup_mode": database}
    async with running_env(app_database_url, signup_mode=environment) as env:
        with stored(database_url, **rows):
            assert (await Browser(env).get(PUBLIC)).json()["signup_mode"] == effective


# --- sign-up ----------------------------------------------------------------------------------


async def test_the_database_at_closed_refuses_password_sign_ups(
    clean_auth: None, app_database_url: str, database_url: str
) -> None:
    for environment in ("invite", "open"):
        async with running_env(app_database_url, signup_mode=environment) as env:
            code = await invite(env)
            with stored(database_url, signup_mode="closed"):
                response = await sign_up(env, code)
                assert (response.status_code, reason(response)) == (403, "signups_closed")
            assert await env.scalar("SELECT count(*) FROM pending_signups") == 0
            assert await env.scalar("SELECT count(*) FROM invites WHERE used_at IS NOT NULL") == 0


async def test_the_database_at_invite_tightens_an_open_environment(
    clean_auth: None, app_database_url: str, database_url: str, smtp: SmtpFake
) -> None:
    async with running_env(
        app_database_url,
        signup_mode="open",
        smtp_host="127.0.0.1",
        smtp_port=smtp.port,
        smtp_tls="none",
    ) as env:
        with stored(database_url, signup_mode="invite"):
            refused = await sign_up(env, None)
            assert (refused.status_code, reason(refused)) == (400, "invalid_invite")
            assert (await sign_up(env, await invite(env))).status_code == 202


async def test_the_database_cannot_open_sign_ups(
    clean_auth: None, app_database_url: str, database_url: str
) -> None:
    """Threats: T94."""
    async with running_env(app_database_url, signup_mode="invite") as env:
        with stored(database_url, signup_mode="open"):  # not a storable value
            response = await sign_up(env, None)
            assert (response.status_code, reason(response)) == (400, "invalid_invite")
    async with running_env(app_database_url, signup_mode="closed") as env:
        with stored(database_url, signup_mode="invite"):
            response = await sign_up(env, await invite(env))
            assert (response.status_code, reason(response)) == (403, "signups_closed")


async def test_the_database_at_closed_refuses_oauth_sign_ups_even_with_an_invite(
    clean_auth: None, app_database_url: str, database_url: str
) -> None:
    fake = FakeProviders(CLIENT_IDS)
    for environment in ("invite", "open"):
        async with running_env(
            app_database_url,
            signup_mode=environment,
            oauth_google_client_id=CLIENT_IDS["google"],
            oauth_google_client_secret=fake.secret,
        ) as env:
            env.app.state.oauth.transport = fake.transport
            code = await invite(env)
            with stored(database_url, signup_mode="closed"):
                browser = Browser(env)
                response = await sign_in_with(browser, fake, "google", invite=code)
                assert location(response) == "/signin?oauth=invite_required"
                assert not browser.signed_in
                assert await env.scalar("SELECT count(*) FROM users") == 0
                assert await env.scalar("SELECT used_at IS NULL FROM invites") is True
            await env.run("DELETE FROM invites")


async def test_the_database_at_invite_tightens_an_open_environment_for_oauth(
    clean_auth: None, app_database_url: str, database_url: str
) -> None:
    fake = FakeProviders(CLIENT_IDS)
    async with running_env(
        app_database_url,
        signup_mode="open",
        oauth_google_client_id=CLIENT_IDS["google"],
        oauth_google_client_secret=fake.secret,
    ) as env:
        env.app.state.oauth.transport = fake.transport
        with stored(database_url, signup_mode="invite"):
            response = await sign_in_with(Browser(env), fake, "google")
            assert location(response) == "/signin?oauth=invite_required"
            assert await env.scalar("SELECT count(*) FROM users") == 0


async def test_a_flow_started_under_invite_is_refused_at_the_callback_once_closed_is_stored(
    clean_auth: None, app_database_url: str, database_url: str
) -> None:
    """The callback is the authoritative gate: the flow carries a valid invite from its start,
    then ``closed`` is stored before the provider returns. Nothing is created."""
    fake = FakeProviders(CLIENT_IDS)
    async with running_env(
        app_database_url,
        signup_mode="invite",
        oauth_google_client_id=CLIENT_IDS["google"],
        oauth_google_client_secret=fake.secret,
    ) as env:
        env.app.state.oauth.transport = fake.transport
        code = await invite(env)
        browser = Browser(env)
        started = await start(browser, "google", "sign_in", invite_code=code)
        assert started.status_code == 200
        assert await env.scalar("SELECT invite_id IS NOT NULL FROM oauth_flows") is True
        with stored(database_url, signup_mode="closed"):
            response = await browser.get(fake.complete(started.json()["authorization_url"]))
        assert location(response) == "/signin?oauth=invite_required"
        assert not browser.signed_in
        assert await env.scalar("SELECT count(*) FROM users") == 0
        assert await env.scalar("SELECT count(*) FROM pending_signups") == 0
        assert await env.scalar("SELECT used_at IS NULL FROM invites") is True


# --- pending sign-ups: closed stops confirmation, a hand-off's invite is always consumed ------


async def hand_off_row(env: Env, fake: FakeProviders, smtp: SmtpFake, code: str) -> tuple[Any, str]:
    """A provider hand-off with an invite, its email filled in: the browser and the mail token."""
    browser = Browser(env)
    response = await sign_in_with(browser, fake, "microsoft", invite=code)
    assert location(response) == "/signin?oauth=email_needed"
    sent = await browser.post("/api/auth/oauth/pending/email", {"email": "new@example.com"})
    assert sent.status_code == 202
    await asyncio.sleep(0.3)
    [mail] = smtp.to("new@example.com")
    return browser, mail.token()


def provider_env(
    app_database_url: str, fake: FakeProviders, smtp: SmtpFake, **extra: object
) -> Any:
    return running_env(
        app_database_url,
        smtp_host="127.0.0.1",
        smtp_port=smtp.port,
        smtp_tls="none",
        oauth_microsoft_client_id=CLIENT_IDS["microsoft"],
        oauth_microsoft_client_secret=fake.secret,
        **extra,
    )


async def test_a_provider_confirmation_is_refused_while_closed_and_keeps_its_row_and_invite(
    clean_auth: None, app_database_url: str, database_url: str, smtp: SmtpFake
) -> None:
    fake = FakeProviders(CLIENT_IDS)
    async with provider_env(app_database_url, fake, smtp) as env:
        env.app.state.oauth.transport = fake.transport
        browser, token = await hand_off_row(env, fake, smtp, await invite(env))
        with stored(database_url, signup_mode="closed"):
            refused = await browser.post("/api/auth/signup/confirm", {"token": token})
            assert (refused.status_code, reason(refused)) == (403, "signups_closed")
            assert await env.scalar("SELECT count(*) FROM pending_signups") == 1
            assert await env.scalar("SELECT count(*) FROM users") == 0
            assert await env.scalar("SELECT used_at IS NULL FROM invites") is True
        # Sign-ups reopen before the row expires: it confirms.
        assert (await browser.post("/api/auth/signup/confirm", {"token": token})).status_code == 200
        assert await env.scalar("SELECT count(*) FROM users") == 1
        assert await env.scalar("SELECT used_at IS NOT NULL FROM invites") is True


async def test_a_hand_off_that_carried_an_invite_consumes_it_whatever_the_mode_is_by_then(
    clean_auth: None, app_database_url: str, database_url: str, smtp: SmtpFake
) -> None:
    fake = FakeProviders(CLIENT_IDS)
    async with provider_env(app_database_url, fake, smtp, signup_mode="open") as env:
        env.app.state.oauth.transport = fake.transport
        code = await invite(env)
        with stored(database_url, signup_mode="invite"):  # the effective mode is invite
            browser, token = await hand_off_row(env, fake, smtp, code)
            assert await env.scalar("SELECT invite_id IS NOT NULL FROM pending_signups") is True
        # The stored row is gone: the effective mode is the environment's, open.
        assert (await browser.post("/api/auth/signup/confirm", {"token": token})).status_code == 200
        assert await env.scalar("SELECT count(*) FROM users") == 1
        assert await env.scalar("SELECT used_at IS NOT NULL FROM invites") is True
        assert await env.scalar("SELECT used_by IS NOT NULL FROM invites") is True


# --- song picking -----------------------------------------------------------------------------


def put(songenv: Any, value: Any) -> None:
    songenv.sql(
        "INSERT INTO app_settings (key, value) VALUES ('song_picking', CAST(:v AS jsonb))",
        v=json.dumps(value),
    )


def refused(response: Any) -> bool:
    return response.status_code == 409 and response.json()["detail"]["reason"] == "song_picking_off"


def take_feature(songenv: Any, rider: Any) -> None:
    """Remove the ``song_picking`` feature entitlement only; a plan grant stays."""
    songenv.sql(
        "DELETE FROM entitlements WHERE owner_id = :o AND 'song_picking' = ANY (features)",
        o=rider.user_id,
    )


@pytest.fixture
def clean_settings(songenv: Any) -> Iterator[None]:
    songenv.sql("DELETE FROM app_settings")
    yield
    songenv.sql("DELETE FROM app_settings")


@pytest.mark.usefixtures("songenv", "clean_settings")
class TestSongPicking:
    """``admit``: ``granted`` needs the feature, ``on`` admits everyone, ``off`` refuses everyone,
    and the environment's kill switch beats every stored value."""

    async def test_the_default_is_granted_to_users_with_the_feature_only(
        self, songenv: Any
    ) -> None:
        with_feature = await songenv.rider()
        without = await songenv.rider()
        take_feature(songenv, without)
        assert (await with_feature.create()).status_code == 201
        assert refused(await without.create())

    async def test_on_admits_users_without_the_feature(self, songenv: Any) -> None:
        rider = await songenv.rider()
        take_feature(songenv, rider)
        put(songenv, "on")
        assert (await rider.create()).status_code == 201

    async def test_off_refuses_even_users_with_the_feature(self, songenv: Any) -> None:
        rider = await songenv.rider()
        put(songenv, "off")
        assert refused(await rider.create())
        assert songenv.sql("SELECT count(*) FROM jobs").scalar_one() == 0

    async def test_the_environment_kill_switch_beats_the_database(self, songenv: Any) -> None:
        rider = await songenv.rider()
        put(songenv, "on")
        songs.configure(songs.SongConfig(enabled=False))
        assert refused(await rider.create())

    async def test_an_invalid_stored_value_falls_back_to_granted(self, songenv: Any) -> None:
        with_feature = await songenv.rider()
        without = await songenv.rider()
        take_feature(songenv, without)
        put(songenv, "whatever")
        assert (await with_feature.create()).status_code == 201
        assert refused(await without.create())

    async def test_revise_songs_follows_the_flag_too(self, songenv: Any) -> None:
        rider = await songenv.rider()
        class_id, base, _ = await picked(songenv, rider)  # admitted while it had the feature
        jobs = songenv.sql("SELECT count(*) FROM jobs").scalar_one()
        take_feature(songenv, rider)
        # Under the default ``granted`` a user without the feature is refused, with no job.
        assert refused(await change(rider, class_id, base))
        assert songenv.sql("SELECT count(*) FROM jobs").scalar_one() == jobs
        # ``off`` refuses a user who has the feature.
        songenv.sql(
            "INSERT INTO entitlements (owner_id, features, source) "
            "VALUES (:o, ARRAY['song_picking'], 'grant')",
            o=rider.user_id,
        )
        put(songenv, "off")
        assert refused(await change(rider, class_id, base))
        assert songenv.sql("SELECT count(*) FROM jobs").scalar_one() == jobs
