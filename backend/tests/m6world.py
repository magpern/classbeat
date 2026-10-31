"""The world of the M6 security suite (issue #339): every fake of the project in one running app,
and the full fake flows driven through it with their secrets known.

``spot`` (overriding the fixture of the same name from ``spotifytools`` in the modules that import
it) builds the app with everything on: song picking, the admin area (maint credentials), SMTP, the
three OAuth sign-in providers (client secrets), and the in-process fake Spotify. ``world`` adds the
fake MOWL (sign-in, music, programs) behind the worker's real allowlist opener and the save kind.

``full_flows`` then plays what a user and an admin do, in the order the product does it: password
sign-in (a wrong password first), password reset by mail, OAuth sign-up with a client secret, MOWL
connect (a wrong password first), Spotify connect (a refusal first), a song pick, its approval, the
design run, Save, and an admin session with TOTP (users, audit, usage, jobs, invites, codes,
settings, models). Every secret it creates or uses is recorded in ``Known`` as it appears;
``Capture``
keeps every log record, rendered every way the project can print one, and every HTTP response.

Fakes only: the autouse network guard stays on, nothing leaves loopback.
"""

import asyncio
import base64
import json
import logging
import urllib.request
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from authtools import APP_SECRET, TEST_KEK, Env, running_env, session_cookie
from classbeat import auth, saving
from classbeat.egress import WORKER_ALLOWLIST, build_allowlist_opener
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.log import JsonFormatter, configure_logging
from classbeat.spotify.http import SpotifyHttp
from flows import PASSWORD
from flows import Browser as WebBrowser
from jobapi import Browser as JobBrowser
from mfatools import Actor, admin_with_totp
from mowlfake import EMAIL as MOWL_EMAIL
from mowlfake import PASSWORD as MOWL_PASSWORD
from mowlfake import FakeMowl
from oauthtools import CLIENT_IDS, make_invite, sign_in_with
from oidcfake import FakeProviders
from optools import TOKEN_A
from savetools import FakeMowlPrograms
from smtpfake import SmtpFake
from songtools import Network, Rider, SongEnv
from spotifyfake import CLIENT_A, USER_A, spotify_client
from spotifytools import (
    CONSENT,
    START,
    RecordingHTTPS,
    Spot,
    make_fake,
)

RESET_PHRASE = "second-horse-battery-staple-9"
BAD_PHRASE = "wrong-horse-battery-staple-0"
MOWL_BAD_PHRASE = "not-the-mowl-password-77"
RIDER = "rider@example.com"
CHANGER = "changer@example.com"


# --- what is known -------------------------------------------------------------------------------


@dataclass
class Known:
    """Secrets by name. ``shown_to_owner`` ones are in a response by design (a session cookie in
    ``Set-Cookie``, the TOTP key at enrolment, a new invite code); the others are in no response."""

    values: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    shown_to_owner: set[str] = field(default_factory=lambda: set[str]())

    def add(self, name: str, value: str | None, *, shown: bool = False) -> None:
        if not value:
            return
        self.values[name] = value
        if shown:
            self.shown_to_owner.add(name)

    def forms(self, names: set[str] | None = None) -> dict[str, list[str]]:
        """Each secret as the forms it could be written in: as is, URL-encoded, base64 and hex of
        its bytes (any case). Name -> forms."""
        out: dict[str, list[str]] = {}
        for name, value in self.values.items():
            if names is not None and name not in names:
                continue
            raw = value.encode()
            out[name] = [
                value,
                quote(value, safe=""),
                base64.b64encode(raw).decode().rstrip("="),
                base64.urlsafe_b64encode(raw).decode().rstrip("="),
                raw.hex(),
                raw.hex().upper(),
            ]
        return out

    @property
    def never_returned(self) -> set[str]:
        return set(self.values) - self.shown_to_owner


def leaks(haystack: str | bytes, known: Known, names: set[str] | None = None) -> list[str]:
    """Names of the secrets that appear in ``haystack`` in any form."""
    data = haystack if isinstance(haystack, bytes) else haystack.encode("utf-8", "replace")
    found: list[str] = []
    for name, forms in known.forms(names).items():
        if any(form.encode() in data for form in forms):
            found.append(name)
    return sorted(found)


# --- capture ---------------------------------------------------------------------------------


@dataclass
class Seen:
    method: str
    path: str
    status: int
    headers: list[tuple[str, str]]
    body: bytes


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@dataclass
class Capture:
    """Every log record, rendered four ways, and every HTTP response."""

    collector: _Collect = field(default_factory=_Collect)
    stream_lines: list[str] = field(default_factory=lambda: list[str]())
    responses: list[Seen] = field(default_factory=lambda: list[Seen]())

    def logs(self) -> str:
        """What the project's own formatter wrote, plus the raw message, every attribute, and the
        unredacted traceback of each record: the formatter masks by key, so a leak under another
        key, or in a traceback, must still show."""
        formatter = JsonFormatter()
        plain = logging.Formatter()
        parts: list[str] = list(self.stream_lines)
        for record in self.collector.records:
            parts.append(record.getMessage())
            parts.append(formatter.format(record))
            parts.append(repr(record.__dict__))
            if record.exc_info:
                parts.append(plain.formatException(record.exc_info))
        return "\n".join(parts)

    def log_count(self) -> int:
        return len(self.collector.records)

    def error_responses(self) -> list[Seen]:
        return [r for r in self.responses if r.status >= 400]


@contextmanager
def capture(monkeypatch: pytest.MonkeyPatch) -> Iterator[Capture]:
    """Log capture as production configures it (``configure_logging``: the JSON handler, the
    library loggers quiet, SQL never logged) at DEBUG, the strictest a deployment can set, and
    response capture on every ``httpx`` client of the test (the app's own clients are ASGI)."""
    import io

    cap = Capture()
    stream = io.StringIO()
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    configure_logging("DEBUG", stream)
    root.addHandler(cap.collector)
    # A logger that does not propagate (uvicorn's, a library's) never reaches the root handlers:
    # give each one the collector and the production JSON handler too.
    detached: list[logging.Logger] = []
    for logger in [
        lg for lg in logging.root.manager.loggerDict.values() if isinstance(lg, logging.Logger)
    ]:
        if not logger.propagate:
            logger.addHandler(cap.collector)
            for handler in root.handlers:
                if handler is not cap.collector:
                    logger.addHandler(handler)
            detached.append(logger)
    original = httpx.AsyncClient.send

    async def send(
        self: httpx.AsyncClient, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        response = await original(self, request, **kwargs)
        if not kwargs.get("stream", False) and "text/event-stream" not in response.headers.get(
            "content-type", ""
        ):
            cap.responses.append(
                Seen(
                    request.method,
                    request.url.path,
                    response.status_code,
                    [(k, v) for k, v in response.headers.multi_items()],
                    response.content,
                )
            )
        return response

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        yield cap
    finally:
        cap.stream_lines = stream.getvalue().splitlines()
        for logger in detached:
            for handler in list(logger.handlers):
                if handler is cap.collector or handler in root.handlers:
                    logger.removeHandler(handler)
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in saved[0]:
            root.addHandler(handler)
        root.setLevel(saved[1])


# --- the world -------------------------------------------------------------------------------


class M6Network(urllib.request.BaseHandler):
    """The worker's network and the web app's MOWL sign-in: Spotify's hosts first, then MOWL
    (sign-in, programs and segments, the music reads)."""

    def __init__(self, songs: SongEnv, programs: FakeMowlPrograms, login: FakeMowl) -> None:
        self.network = Network(songs.spot.library, songs.mowl)
        self.programs = programs
        self.login = login

    def __hash__(self) -> int:
        return id(self)

    def https_open(self, req: urllib.request.Request) -> Any:
        answer = self.network.spotify.https_open(req)
        if answer is not None:
            return answer
        path = urlsplit(req.full_url).path
        if path.startswith("/v1/Authentication/"):
            data = req.data if isinstance(req.data, bytes) else b""
            form = {k: v[0] for k, v in parse_qs(data.decode()).items()}
            status, body = self.login.answer(path, form, req.get_header("Authorization"))
            return self.network.mowl._answer(req, status, body)  # pyright: ignore[reportPrivateUsage]
        if path.startswith(("/v1/programs", "/v1/segments")):
            return self.programs.https_open(req)
        return self.network.mowl.https_open(req)


@dataclass
class World:
    songs: SongEnv
    programs: FakeMowlPrograms
    login: FakeMowl
    smtp: SmtpFake
    oidc: FakeProviders
    clients: list[httpx.AsyncClient] = field(default_factory=lambda: list[httpx.AsyncClient]())

    @property
    def env(self) -> Env:
        return self.songs.spot.env

    @property
    def spot(self) -> Spot:
        return self.songs.spot

    @property
    def sql(self) -> Any:
        return self.songs.sql

    def client(self) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.env.app), base_url=self.env.client.base_url
        )
        self.clients.append(client)
        return client


@pytest.fixture
async def spot(
    clean_auth: None,
    app_database_url: str,
    maint_database_url: str,
    smtp: SmtpFake,
    fake: FakeProviders,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Spot]:
    """The ``spot`` of the other Spotify tests with the whole product on."""
    spotify = make_fake()
    library = RecordingHTTPS(spotify)
    monkeypatch.setattr(
        urllib.request, "_opener", build_allowlist_opener(WORKER_ALLOWLIST, https_handler=library)
    )
    maint = make_url(maint_database_url)
    oauth: dict[str, str] = {}
    for name, client_id in CLIENT_IDS.items():
        oauth[f"oauth_{name}_client_id"] = client_id
        oauth[f"oauth_{name}_client_secret"] = fake.secret
    async with running_env(
        app_database_url,
        song_picking=True,
        jobs_per_user=16,
        smtp_host="127.0.0.1",
        smtp_port=smtp.port,
        smtp_tls="none",
        maint_database_url=maint.set(password=None).render_as_string(hide_password=False),
        database_maint_password=maint.password,
        **oauth,
    ) as env:
        env.app.state.oauth.transport = fake.transport
        http = SpotifyHttp(lambda total: spotify_client(spotify, total_seconds=total))
        env.app.state.spotify_http = http
        found = Spot(env, spotify, http, library)
        try:
            yield found
        finally:
            for client in found.clients:
                await client.aclose()


@pytest.fixture
async def world(
    songenv: SongEnv,
    smtp: SmtpFake,
    fake: FakeProviders,
    clock: list[float],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[World]:
    programs = FakeMowlPrograms()
    login = FakeMowl(token=TOKEN_A)
    network = M6Network(songenv, programs, login)
    monkeypatch.setattr(
        urllib.request, "_opener", build_allowlist_opener(WORKER_ALLOWLIST, https_handler=network)
    )
    saving.configure(saving.Runtime(songenv.ring))
    saving.configure_listing_lag(None)
    made = World(songenv, programs, login, smtp, fake)
    try:
        yield made
    finally:
        saving.configure(None)
        saving.configure_listing_lag(None)
        for client in made.clients:
            await client.aclose()


# --- the flows ---------------------------------------------------------------------------------


def _record_pkce_verifiers(world: World, known: Known) -> None:
    """The PKCE ``code_verifier`` is made by the server and only ever sent to the token endpoint:
    the fakes' token handlers see it, so it is recorded there as it arrives."""
    spotify_handle = world.spot.fake.handle

    def handle(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Any:
        if body:
            for value in parse_qs(body.decode("utf-8", "replace")).get("code_verifier", []):
                known.add(f"spotify_code_verifier_{len(known.values)}", value)
        return spotify_handle(method, url, headers, body)

    world.spot.fake.handle = handle  # pyright: ignore[reportAttributeAccessIssue]
    oidc_token = world.oidc._token  # pyright: ignore[reportPrivateUsage]

    def token(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode("utf-8", "replace"))
        for value in form.get("code_verifier", []):
            known.add(f"oidc_code_verifier_{len(known.values)}", value)
        return oidc_token(request)

    world.oidc._token = token  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]


def query_of(location: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


async def full_flows(world: World, admin: Connection, known: Known) -> dict[str, Any]:
    """Plays every flow; ``known`` gets each secret as it is made. Returns the ids the checks
    below need (``rider``, ``job``, ``class_id``, ``revision``, ...)."""
    env, spot = world.env, world.spot
    known.add("app_secret_key", APP_SECRET)
    known.add("connection_kek", TEST_KEK.hex())
    known.add("oauth_client_secret", world.oidc.secret)
    known.add("mowl_password", MOWL_PASSWORD)
    known.add("mowl_token", world.login.token)
    known.add("mowl_login_ticket", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    out: dict[str, Any] = {}
    _record_pkce_verifiers(world, known)

    # --- password sign-in, with a wrong password first -------------------------------------
    hashed = await env.app.state.password_hasher.hash(PASSWORD)
    rider_id = await env.user(RIDER, password_hash=hashed)
    out["rider"] = rider_id
    known.add("password", PASSWORD)
    known.add("wrong_password", BAD_PHRASE)
    web = WebBrowser(env)
    refused = await web.sign_in(RIDER, BAD_PHRASE)
    assert refused.status_code == 401, refused.text
    ok = await web.sign_in(RIDER)
    assert ok.status_code == 200, ok.text
    session_token = web.cookies[auth.SESSION_COOKIE]
    known.add("session_token", session_token, shown=True)
    csrf = await web.csrf()
    known.add("csrf_token", csrf, shown=True)
    for name, value in web.cookies.items():
        if name != auth.SESSION_COOKIE:
            known.add(f"cookie:{name}", value, shown=True)

    # --- password reset by mail ----------------------------------------------------------------
    changer_hash = await env.app.state.password_hasher.hash(PASSWORD)
    await env.user(CHANGER, password_hash=changer_hash)
    anonymous = WebBrowser(env)
    asked = await anonymous.post("/api/auth/password/reset/request", {"email": CHANGER})
    assert asked.status_code in (200, 202, 204), asked.text
    mail_token = world.smtp.to(CHANGER)[0].token()
    known.add("reset_link_token", mail_token)
    known.add("new_password", RESET_PHRASE)
    assert (
        await anonymous.post("/api/auth/password/reset/info", {"token": "not-" + mail_token})
    ).status_code == 400
    done = await anonymous.post(
        "/api/auth/password/reset/complete",
        {"token": mail_token, "password": RESET_PHRASE, "remove_methods": []},
    )
    assert done.status_code == 200, done.text
    again = await anonymous.post(  # the link works once
        "/api/auth/password/reset/complete",
        {"token": mail_token, "password": RESET_PHRASE, "remove_methods": []},
    )
    assert again.status_code == 400, again.text
    assert (await WebBrowser(env).sign_in(CHANGER, RESET_PHRASE)).status_code == 200

    # --- OAuth sign-up with an invite (client secret, code, state) ------------------------
    invite = await make_invite(env)
    known.add("invite_code", invite, shown=True)
    oauth_browser = WebBrowser(env)
    started = await oauth_browser.post(
        "/api/auth/oauth/google/start", {"purpose": "sign_in", "invite_code": invite}
    )
    assert started.status_code == 200, started.text
    authorize = started.json()["authorization_url"]
    authorize_query = query_of(authorize)
    # state, nonce and the code challenge are in the authorization URL by design
    known.add("oauth_state", authorize_query.get("state"), shown=True)
    known.add("oauth_nonce", authorize_query.get("nonce"), shown=True)
    callback = world.oidc.complete(authorize)
    callback_query = query_of(callback)
    known.add("oauth_code", callback_query.get("code"))
    landed = await oauth_browser.get(callback)
    assert landed.status_code == 303, landed.text
    _ = sign_in_with  # the helper this inlines

    # --- MOWL connect: wrong password, then right -----------------------------------------------
    actor = Actor(env, session_token, world.client())
    job_browser = JobBrowser(env, session_token, world.client(), rider_id)
    known.add("mowl_wrong_password", MOWL_BAD_PHRASE)
    bad = await actor.post(
        "/api/connections/mowl", {"email": MOWL_EMAIL, "password": MOWL_BAD_PHRASE}
    )
    assert bad.status_code == 400, bad.text
    good = await actor.post(
        "/api/connections/mowl", {"email": MOWL_EMAIL, "password": MOWL_PASSWORD}
    )
    assert good.status_code == 200, good.text
    mowl_connection = UUID(good.json()["id"])

    # --- Spotify connect: an unknown app first, then the real dance ------------------------
    admin.execute(
        text("INSERT INTO entitlements (owner_id, features, source) "
             "VALUES (:o, ARRAY['song_picking'], 'grant')"),
        {"o": rider_id},
    )  # fmt: skip
    admin.execute(
        text("INSERT INTO entitlements (owner_id, plan_id, source) VALUES (:o, 'pro', 'grant')"),
        {"o": rider_id},
    )
    unknown_app = await actor.post(START, {"client_id": "f" * 32, "confirm_account_change": False})
    known.add("unused_marker", None)
    assert unknown_app.status_code in (200, 400, 409), unknown_app.text
    started_url = await spot.start(actor, CLIENT_A)
    authorize_spotify = query_of(started_url)
    known.add("spotify_state", authorize_spotify.get("state"), shown=True)
    known.add("spotify_code_challenge", authorize_spotify.get("code_challenge"), shown=True)
    callback_query = spot.approve(started_url, USER_A)
    known.add("spotify_code", callback_query.get("code"))
    known.add("spotify_callback_state", callback_query.get("state"), shown=True)
    outcome = await spot.callback(actor, callback_query)
    assert outcome.get("result") == "connected", outcome
    consent = await actor.call("PUT", CONSENT)
    assert consent.status_code in (200, 204), consent.text
    spotify_id = admin.execute(
        text("SELECT id FROM connections WHERE owner_id = :o AND provider = 'spotify'"),
        {"o": rider_id},
    ).scalar_one()
    for token in list(spot.fake.access) + list(spot.fake.refresh) + list(spot.fake.codes):
        known.add(f"spotify_token:{token[:12]}", token)

    # --- a pick, its approval, the design run -------------------------------------------------
    job_browser.connection_id = mowl_connection
    rider = Rider(job_browser, spotify_id, mowl_connection)
    songs = world.songs
    job_id, revision_id, digest = await songs.waiting(rider)
    approved = await rider.approve(job_id, revision_id, digest)
    assert approved.status_code == 200, approved.text
    from songtools import design_script

    songs.script = design_script()
    row = await songs.run(job_id)
    assert row.state == "completed", (row.state, row.error_category)
    out["pick_job"] = job_id
    classes = admin.execute(
        text("SELECT class_id FROM jobs WHERE id = :j"), {"j": job_id}
    ).scalar_one()
    out["class_id"] = classes
    draft = admin.execute(
        text("SELECT id, content_hash FROM class_revisions WHERE class_id = :c AND kind = 'draft' "
             "ORDER BY number DESC LIMIT 1"),
        {"c": classes},
    ).one()  # fmt: skip
    out["revision"] = draft.id

    # --- Save --------------------------------------------------------------------------------
    body = {
        "kind": saving.KIND,
        "input": {
            "class_id": str(classes),
            "revision_id": str(draft.id),
            "content_hash": bytes(draft.content_hash).hex(),
            "mowl_connection_id": str(mowl_connection),
        },
    }
    created = await job_browser.post("/api/jobs", body, {"Idempotency-Key": "m6-save-" + "k" * 14})
    assert created.status_code == 201, created.text
    save_id = UUID(created.json()["id"])
    registry = KindRegistry()
    registry.register(saving.SAVE_WORKOUT)
    registry.register(saving.UPDATE_WORKOUT)
    lease = env.settings.job_lease_seconds
    claimed = await queue.claim(env.factory, "m6-w", lease, [saving.KIND, saving.UPDATE_KIND])
    assert claimed is not None
    await execute(
        Execution(claimed), env.factory, registry, RunConfig(lease_seconds=lease), asyncio.Event()
    )
    saved = admin.execute(text("SELECT state FROM jobs WHERE id = :j"), {"j": save_id}).scalar_one()
    assert saved == "completed", saved
    out["save_job"] = save_id
    assert (await job_browser.get(f"/api/jobs/{save_id}")).status_code == 200

    # --- the error paths of the owned routes -------------------------------------------------
    assert (
        await job_browser.get("/api/jobs/00000000-0000-4000-8000-000000000000")
    ).status_code == 404
    assert (await actor.post("/api/jobs/not-a-uuid/cancel")).status_code == 404
    stale = await rider.approve(job_id, revision_id, "0" * 64)  # the pick finished long ago
    assert stale.status_code in (404, 409), stale.text
    no_csrf = await world.client().post(  # a write without the CSRF token of the session
        "/api/connections/mowl",
        headers=session_cookie(session_token) | {"Origin": str(env.client.base_url).rstrip("/")},
        json={"email": MOWL_EMAIL, "password": MOWL_PASSWORD},
    )
    assert no_csrf.status_code == 403, no_csrf.text
    odd_body = await actor.post(
        "/api/connections/mowl", {"email": "not an address", "password": MOWL_PASSWORD}
    )
    assert odd_body.status_code == 422, odd_body.text
    spotify_bad = await actor.client.get(
        "/oauth/spotify/callback",
        params={"code": "bogus-code", "state": "bogus-state"},
        headers=session_cookie(actor.token),
    )
    actor.client.cookies.clear()
    assert spotify_bad.status_code in (302, 400), spotify_bad.text

    # --- an admin: TOTP, a step-up, the whole area ---------------------------------------------
    admin_actor, secret, codes, admin_id = await admin_with_totp(env, _maker(world))
    known.add("totp_secret", secret, shown=True)
    for i, code in enumerate(codes):
        known.add(f"recovery_code_{i}", code, shown=True)
    known.add("admin_session_token", admin_actor.token, shown=True)
    wrong_code = await admin_actor.post("/api/auth/mfa/stepup", {"code": "000000"})
    assert wrong_code.status_code in (400, 401, 403), wrong_code.text
    admin.execute(text("UPDATE sessions SET stepup_at = now() WHERE user_id = :u"), {"u": admin_id})
    for path in (
        "/api/admin/whoami",
        "/api/admin/users",
        f"/api/admin/users/{rider_id}",
        "/api/admin/audit",
        "/api/admin/usage",
        "/api/admin/jobs",
        "/api/admin/invites",
        "/api/admin/redeem-codes",
        "/api/admin/settings",
        "/api/admin/models",
    ):
        answer = await admin_actor.get(path)
        assert answer.status_code == 200, (path, answer.status_code, answer.text[:200])
    new_invite = await admin_actor.post("/api/admin/invites", {})
    assert new_invite.status_code in (200, 201), new_invite.text
    for value in _strings(new_invite.json()):
        if len(value) >= 16 and " " not in value:
            known.add("new_invite", value, shown=True)
            break
    refused_put = await admin_actor.call(
        "PUT", "/api/admin/settings/signup_mode", {"value": "open"}
    )
    assert refused_put.status_code in (409, 422), refused_put.text
    plain = Actor(env, session_token, world.client())
    assert (await plain.get("/api/admin/users")).status_code == 403
    assert (await plain.get("/api/admin/audit")).status_code == 403
    out["admin"] = admin_id
    return out


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():  # pyright: ignore[reportUnknownVariableType]
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:  # pyright: ignore[reportUnknownVariableType]
            yield from _strings(item)


def _maker(world: World) -> Any:
    async def make(user_id: UUID, *, mfa: bool = False) -> Actor:
        issued = await world.env.login(user_id, mfa=mfa)
        return Actor(world.env, issued.token, world.client())

    return make


_ = (json, session_cookie)
