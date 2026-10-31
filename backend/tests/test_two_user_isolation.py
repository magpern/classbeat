"""Roadmap section 1 and M2 done-when: two users at once, nothing crosses (issue #67).

Two users run concurrently (asyncio) through the real FastAPI app (an async client), on a real
Postgres as the app role under RLS, connecting with different fake credentials and using MOWL
(``connections.use``, and the real route ``GET /api/music/mowl-playlists``) against a fake
upstream (``isofake``) that records whose credential each call carried. ``verify``
asserts that nothing crossed: upstream calls, rows, decryption, events and logs. Rate limits and
owned endpoints have their own tests. The check fails if a token or a row crosses users:
``test_the_check_detects_*`` run the same scenario against deliberately broken test doubles and
require ``verify`` to fail. Nothing broken exists in production code.
"""

import asyncio
import io
import logging
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
from mowl import api as library_api
from sqlalchemy import create_engine, text

from authtools import Env
from classbeat import connections
from classbeat.connections import ConnectionFailure
from classbeat.db import user_session
from classbeat.log import JsonFormatter
from ctx import make_context
from isofake import ACCOUNTS, ALICE, BOB, Account, TwoMowl

CONNECT = "/api/connections/mowl"
USES = 4  # library calls per user through ``connections.use``, and route reads per user
TIMEOUT = 60  # seconds for the whole scenario: a user failing early must not hang the other
BAD_ATTEMPTS = 2  # wrong-password connects per user (their own rate-limit slots)
pytestmark = pytest.mark.usefixtures("two_mowl")


class Everything(logging.Handler):
    """Every log record, formatted by the production formatter as well."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []
        self.out = io.StringIO()
        self.formatter_ = JsonFormatter()

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
        self.out.write(self.formatter_.format(record) + "\n")

    def everything(self) -> str:
        return self.out.getvalue() + "".join(repr(vars(r)) + r.getMessage() for r in self.records)


@pytest.fixture
def logs() -> Iterator[Everything]:
    handler = Everything()
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(level)


@dataclass
class Rider:
    """One user of the scenario: the account, the browser, and what the user saw."""

    account: Account
    user_id: UUID
    actor: Any
    seen: list[list[str]] = field(default_factory=lambda: list[list[str]]())
    playlists: list[Any] = field(default_factory=lambda: list[Any]())
    statuses: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    connection_id: UUID | None = None

    @property
    def ctx(self) -> Any:
        return make_context(self.user_id)


def names(workouts: Any) -> list[str]:
    return [w["Name"] for w in workouts]


async def riders(env: Env, make_actor: Any) -> list[Rider]:
    out: list[Rider] = []
    for account in ACCOUNTS:
        user_id = await env.user(f"{account.label}@classbeat.test")
        out.append(Rider(account, user_id, await make_actor(user_id)))
    return out


def creds(account: Account, password: str | None = None) -> dict[str, str]:
    return {"email": account.email, "password": password or account.password}


async def flow(env: Env, rider: Rider, barrier: asyncio.Barrier) -> None:
    """One user's session: wrong passwords, connect, use MOWL repeatedly, read the status."""
    try:
        await session(env, rider, barrier)
    except BaseException:
        await barrier.abort()  # the other user stops waiting for a partner that failed before it
        raise


async def session(env: Env, rider: Rider, barrier: asyncio.Barrier) -> None:
    ring = env.app.state.keyring
    for _ in range(BAD_ATTEMPTS):
        refused = await rider.actor.post(CONNECT, creds(rider.account, "not-the-password"))
        assert refused.status_code == 400
    connected = await rider.actor.post(CONNECT, creds(rider.account))
    rider.statuses["connect"] = connected.status_code
    await barrier.wait()  # both are connected: from here the two users' calls interleave
    for _ in range(USES):
        found = await connections.use(
            env.factory, ring, rider.ctx, "mowl", lambda c: names(library_api.list_workouts(c))
        )
        rider.seen.append(found)
        music = await rider.actor.get("/api/music/mowl-playlists")  # the real route
        rider.statuses["playlists"] = music.status_code
        rider.playlists.append(music.json())
        listed = await rider.actor.get("/api/connections")
        rider.statuses["list"] = listed.status_code
        assert [c["provider"] for c in listed.json()["connections"]] == ["mowl"]


async def run_scenario(env: Env, make_actor: Any) -> list[Rider]:
    people = await riders(env, make_actor)
    barrier = asyncio.Barrier(len(people))
    async with asyncio.timeout(TIMEOUT):
        outcomes = await asyncio.gather(
            *(flow(env, p, barrier) for p in people), return_exceptions=True
        )
    failures = [o for o in outcomes if isinstance(o, BaseException)]
    if failures:  # the user that failed, not the one that found the barrier broken
        raise next(
            (f for f in failures if not isinstance(f, asyncio.BrokenBarrierError)), failures[0]
        )
    for p in people:
        async with user_session(env.factory, p.ctx) as db:
            p.connection_id = (await db.execute(text("SELECT id FROM connections"))).scalar_one()
    return people


def superuser_rows(database_url: str, sql: str, **params: object) -> list[Any]:
    """Rows as the superuser (setup and probes only: it sees what RLS hides)."""
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            result = connection.execute(text(sql), params)
            return list(result) if result.returns_rows else []
    finally:
        engine.dispose()


async def verify(
    env: Env, people: list[Rider], fake: TwoMowl, logs: Everything, database_url: str
) -> None:
    """Raises ``AssertionError`` when anything crossed between the users."""
    alice, bob = people
    ring = env.app.state.keyring

    # 1. Upstream: every call carried its own user's credential, none another's or an unknown.
    expected: dict[tuple[str, str | None, bool], int] = {}
    for p in people:
        label = p.account.label
        expected[("authenticate", label, False)] = BAD_ATTEMPTS
        expected[("authenticate", label, True)] = 1
        expected[("exchange", label, True)] = 1
        expected[("programs", label, True)] = USES
        expected[("playlists", label, True)] = USES
    # The counts are per account and cannot see a symmetric swap (each account's token used by
    # the other user as often as its own): the data each user saw, below, does.
    assert dict(fake.counts()) == expected, f"upstream call counts are off: {fake.counts()}"
    for p in people:  # what each user saw is its own account's data
        assert p.statuses == {"connect": 200, "list": 200, "playlists": 200}
        assert p.seen == [[f"{p.account.label} workout"]] * USES, (
            f"{p.account.label} was shown another account's data"
        )
        own = {
            "playlists": [
                {
                    "spotify_playlist_id": p.account.playlist_id,
                    "name": f"{p.account.label} playlist",
                }
            ]
        }
        assert p.playlists == [own] * USES, (
            f"{p.account.label} was shown another account's playlists"
        )

    # 2. Decryption through A's context yields A's token, never B's (and the reverse).
    for p, other in ((alice, bob), (bob, alice)):
        try:
            opened = await connections.open_connection(env.factory, ring, p.ctx, "mowl")
        except ConnectionFailure as error:
            raise AssertionError(f"{p.account.label}'s own token does not decrypt") from error
        assert opened.credentials.mowl_token == p.account.token
        assert opened.credentials.mowl_token != other.account.token

    # 3. Rows: one connection per user, owned by that user; no lookup by the other's id.
    for p, other in ((alice, bob), (bob, alice)):
        listed = [c.id for c in await connections.list_connections(env.factory, p.ctx)]
        assert listed == [p.connection_id], f"{p.account.label} sees {listed}"
        assert other.connection_id is not None
        assert await connections.find(env.factory, p.ctx, other.connection_id) is None
    owners = superuser_rows(database_url, "SELECT owner_id, id FROM connections")
    assert {(o, i) for o, i in owners} == {(p.user_id, p.connection_id) for p in people}
    events = superuser_rows(
        database_url,
        "SELECT user_id, kind, detail::text FROM auth_events WHERE kind LIKE 'connection_%'",
    )
    for p in people:
        kinds = [k for u, k, _ in events if u == p.user_id]
        assert kinds.count("connection_connected") == 1
        assert kinds.count("connection_failed") == BAD_ATTEMPTS
    assert len(events) == len(people) * (1 + BAD_ATTEMPTS)

    # 4. Logs and stored event details hold neither user's secrets.
    stored = " ".join(str(d) for *_, d in events)
    logged = logs.everything()
    for account in ACCOUNTS:
        for secret in account.secrets:
            assert secret not in logged, f"{account.label}'s secret is in the logs"
            assert secret not in stored, f"{account.label}'s secret is in an event"


async def test_two_users_at_once_never_cross(
    env: Env, make_actor: Any, two_mowl: TwoMowl, logs: Everything, database_url: str
) -> None:
    people = await run_scenario(env, make_actor)
    await verify(env, people, two_mowl, logs, database_url)


async def test_owned_endpoints_do_not_leak_the_other_users_ids(env: Env, make_actor: Any) -> None:
    alice, bob = await riders(env, make_actor)
    bob_session = await env.scalar("SELECT id FROM sessions WHERE user_id = :u", u=bob.user_id)
    await env.run(
        "INSERT INTO identities (user_id, provider, subject) VALUES (:u, 'github', 'bob')",
        u=bob.user_id,
    )
    bob_identity = await env.scalar("SELECT id FROM identities WHERE user_id = :u", u=bob.user_id)
    unknown = "00000000-0000-4000-8000-000000000000"
    for base, owned_id in (
        ("/api/auth/sessions/", bob_session),
        ("/api/auth/identities/", bob_identity),
    ):  # B's id answers exactly like an id that does not exist
        theirs = await alice.actor.call("DELETE", f"{base}{owned_id}")
        nothing = await alice.actor.call("DELETE", f"{base}{unknown}")
        assert (theirs.status_code, theirs.text) == (nothing.status_code, nothing.text)
        assert theirs.status_code == 404
    assert await env.scalar("SELECT count(*) FROM sessions WHERE id = :i", i=bob_session) == 1
    assert await env.scalar("SELECT count(*) FROM identities") == 1
    # Connections are addressed by provider, never by id: A's disconnect cannot touch B's row.
    assert (await bob.actor.post(CONNECT, creds(BOB))).status_code == 200
    assert (await alice.actor.call("DELETE", CONNECT)).status_code == 204
    shown = (await bob.actor.get("/api/connections")).json()["connections"]
    assert [c["status"] for c in shown] == ["connected"]
    assert (await alice.actor.get("/api/connections")).json()["connections"] == []


async def test_one_users_rate_limit_does_not_throttle_the_other(
    env: Env, make_actor: Any, two_mowl: TwoMowl
) -> None:
    alice, bob = await riders(env, make_actor)

    async def hammer() -> list[int]:
        return [
            (await alice.actor.post(CONNECT, creds(ALICE, "wrong"))).status_code for _ in range(6)
        ]

    async def work() -> list[int]:
        return [(await bob.actor.post(CONNECT, creds(BOB))).status_code for _ in range(4)]

    alice_codes, bob_codes = await asyncio.gather(hammer(), work())
    assert alice_codes == [400] * 5 + [429]  # A runs out of its own slots
    assert bob_codes == [200] * 4  # B is never throttled by A's attempts
    calls = two_mowl.counts()
    assert calls[("authenticate", "alice", False)] == 5  # the sixth never reached MOWL
    assert calls[("authenticate", "bob", True)] == 4
    assert ("authenticate", "alice", True) not in calls


async def test_a_user_failing_before_the_barrier_fails_the_run_cleanly(
    env: Env, make_actor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = session

    async def broken(env_: Env, rider: Rider, barrier: asyncio.Barrier) -> None:
        if rider.account is ALICE:
            raise RuntimeError("alice failed before the barrier")
        await real(env_, rider, barrier)

    monkeypatch.setattr(sys.modules[__name__], "session", broken)
    with pytest.raises(RuntimeError, match="alice failed before the barrier"):
        await asyncio.wait_for(run_scenario(env, make_actor), timeout=TIMEOUT / 2)


async def test_the_check_detects_a_token_swapped_upstream(
    env: Env, make_actor: Any, two_mowl: TwoMowl, logs: Everything, database_url: str
) -> None:
    """A deliberately broken upstream hands each user the other's token: ``verify`` must fail."""
    two_mowl.swap_tokens = True
    people = await run_scenario(env, make_actor)
    with pytest.raises(AssertionError, match="was shown another account's data"):
        await verify(env, people, two_mowl, logs, database_url)


async def test_the_check_detects_a_row_that_crossed_users(
    env: Env, make_actor: Any, two_mowl: TwoMowl, logs: Everything, database_url: str
) -> None:
    """Bob's connection row is handed to Alice (as the superuser, standing in for an ownership
    bug): ``verify`` must fail."""
    people = await run_scenario(env, make_actor)
    await verify(env, people, two_mowl, logs, database_url)  # clean before the corruption
    alice, bob = people
    superuser_rows(database_url, "DELETE FROM connections WHERE owner_id = :a", a=alice.user_id)
    superuser_rows(
        database_url,
        "UPDATE connections SET owner_id = :a WHERE owner_id = :b",
        a=alice.user_id,
        b=bob.user_id,
    )
    with pytest.raises(AssertionError, match="own token does not decrypt"):
        await verify(env, people, two_mowl, logs, database_url)


async def test_the_check_detects_a_secret_in_the_logs(
    env: Env, make_actor: Any, two_mowl: TwoMowl, logs: Everything, database_url: str
) -> None:
    """A deliberately leaking log line carries Bob's token: ``verify`` must fail."""
    people = await run_scenario(env, make_actor)
    await verify(env, people, two_mowl, logs, database_url)  # clean first
    logging.getLogger("leaky.double").warning("using token %s", BOB.token)
    with pytest.raises(AssertionError, match="bob's secret is in the logs"):
        await verify(env, people, two_mowl, logs, database_url)
