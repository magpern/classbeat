"""The player events (ADR 0034 section 2, #1058), on the real database: who gets one, the same
transaction as the change, the row outliving its user, and the leased, fenced delivery to a fake
player (``playertools``) with its retries, its 40-attempt and 7-day caps and the 30-day purge."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from authtools import Env
from classbeat import auth
from classbeat.auth.oidc.subject_calc import pairwise_subject
from classbeat.egress import player_test_client
from classbeat.player import outbox
from classbeat.player.delivery import deliver_once
from oidctools import CLIENT_ID, PAIRWISE_KEY, oidc_env
from playertools import GRANT, FakePlayer, event_of, fake_player

DELETED = "urn:classbeat:player:account-deleted"
REVOKED = "urn:classbeat:player:sessions-revoked"
EVENTS = "SELECT kind, sub, state, attempt FROM player_events ORDER BY created_at, kind"


@pytest.fixture
async def penv(clean_auth: None, app_database_url: str, kek_dir: Path) -> AsyncIterator[Env]:
    async with oidc_env(app_database_url, kek_dir, player_base_url="https://player.example") as e:
        yield e


async def player_user(env: Env, email: str = "rider@example.com") -> UUID:
    user_id = await env.user(email)
    await env.run(GRANT, u=user_id)
    return user_id


async def rows(env: Env) -> list[Any]:
    async with env.db() as db:
        return list((await db.execute(text(EVENTS))).all())


async def revoke(env: Env, user_id: UUID) -> None:
    async with env.db() as db:
        await auth.revoke_user_sessions(db, user_id)


async def delete(env: Env, user_id: UUID) -> None:
    async with env.db() as db:
        assert await auth.delete_account(db, user_id)


async def deliver(env: Env, player: FakePlayer, worker: str = "w1", **kwargs: Any) -> Any:
    signer = auth.load_event_signer(env.settings)
    return await deliver_once(
        env.factory, signer, worker, client_factory=player.client_factory(), **kwargs
    )


@pytest.mark.parametrize(("trigger", "urn"), [("revoke", REVOKED), ("delete", DELETED)])
async def test_each_trigger_delivers_a_signed_logout_token(
    penv: Env, trigger: str, urn: str
) -> None:
    user_id = await player_user(penv)
    await (revoke if trigger == "revoke" else delete)(penv, user_id)
    player = await fake_player(penv)
    result = await deliver(penv, player)
    assert (result.outcome, result.error) == ("delivered", None)
    [claims] = player.received
    assert event_of(claims) == urn
    assert claims["sub"] == pairwise_subject(PAIRWISE_KEY, CLIENT_ID, user_id)
    assert await rows(penv) == []  # delivered: the row is gone
    assert await deliver(penv, player) is None


async def test_nobody_without_a_first_issued_token_gets_an_event(penv: Env) -> None:
    plain = await penv.user("plain@example.com")
    consented = await penv.user("consented@example.com")
    await penv.run(
        "INSERT INTO oidc_grants (user_id, client_id, consent_at) VALUES (:u, :c, now())",
        u=consented, c=CLIENT_ID,
    )  # fmt: skip
    for user_id in (plain, consented):
        await revoke(penv, user_id)
        await delete(penv, user_id)
    assert await rows(penv) == []


async def test_a_revocation_waits_while_one_is_pending_and_unleased(penv: Env) -> None:
    user_id = await player_user(penv)
    await revoke(penv, user_id)
    await revoke(penv, user_id)
    assert [r.kind for r in await rows(penv)] == ["sessions_revoked"]
    async with penv.db() as db:
        assert await outbox.claim(db, "w1") is not None  # in flight: a new one is written
    await revoke(penv, user_id)
    assert [r.kind for r in await rows(penv)] == ["sessions_revoked", "sessions_revoked"]


async def test_deletion_replaces_pending_revocations_and_the_row_outlives_the_user(
    penv: Env,
) -> None:
    user_id = await player_user(penv)
    other = await player_user(penv, "other@example.com")
    await revoke(penv, user_id)
    await revoke(penv, other)
    await delete(penv, user_id)
    assert await penv.scalar("SELECT count(*) FROM users WHERE id = :u", u=user_id) == 0
    assert await penv.scalar("SELECT count(*) FROM oidc_grants WHERE user_id = :u", u=user_id) == 0
    subject = pairwise_subject(PAIRWISE_KEY, CLIENT_ID, user_id)
    left = {(r.kind, r.sub) for r in await rows(penv)}
    assert left == {
        ("account_deleted", subject),
        ("sessions_revoked", pairwise_subject(PAIRWISE_KEY, CLIENT_ID, other)),
    }


async def test_the_event_is_written_in_the_transaction_of_the_change(penv: Env) -> None:
    user_id = await player_user(penv)

    class Abort(Exception):
        pass

    async def delete_then_fail() -> None:
        async with penv.db() as db:
            assert await auth.delete_account(db, user_id)
            assert await db.scalar(text("SELECT count(*) FROM player_events")) == 1
            raise Abort

    with pytest.raises(Abort):
        await delete_then_fail()
    assert await penv.scalar("SELECT count(*) FROM users WHERE id = :u", u=user_id) == 1
    assert await rows(penv) == []  # rolled back with the deletion


async def test_a_missing_pairwise_key_refuses_the_change(penv: Env, kek_dir: Path) -> None:
    user_id = await player_user(penv)
    (kek_dir / "oidc_pairwise_key").unlink()
    with pytest.raises(Exception, match="oidc_pairwise_key"):
        await delete(penv, user_id)
    assert await penv.scalar("SELECT count(*) FROM users WHERE id = :u", u=user_id) == 1


async def test_a_retry_reuses_the_jti_with_a_new_iat_after_the_backoff(penv: Env) -> None:
    user_id = await player_user(penv)
    await delete(penv, user_id)
    player = await fake_player(penv)
    player.statuses = [503, 400]
    start = datetime.now(UTC)
    clock = iter([start, start, start + timedelta(minutes=2), start + timedelta(minutes=2)])
    first = await deliver(penv, player, now=lambda: next(clock), jitter=lambda: 0.0)
    assert (first.outcome, first.error) == ("retry", "status_5xx")
    row = await penv.row(
        "SELECT attempt, last_error, available_at, leased_by, lease_version FROM player_events"
    )
    assert (row.attempt, row.last_error, row.leased_by) == (1, "status_5xx", None)
    assert row.available_at == start + timedelta(seconds=30)
    assert await deliver(penv, player) is None  # not due yet
    await penv.run("UPDATE player_events SET available_at = now()")
    second = await deliver(penv, player, now=lambda: next(clock), jitter=lambda: 0.0)
    assert (second.outcome, second.error) == ("retry", "status_4xx")
    one, two = player.received
    assert one["jti"] == two["jti"]
    assert two["iat"] - one["iat"] == 120
    assert await penv.scalar("SELECT attempt FROM player_events") == 2


async def test_a_stale_worker_is_fenced_out(penv: Env) -> None:
    user_id = await player_user(penv)
    await delete(penv, user_id)
    async with penv.db() as db:
        stale = await outbox.claim(db, "w-old")
    assert stale is not None
    await penv.run("UPDATE player_events SET lease_expires_at = now() - interval '1 second'")
    async with penv.db() as db:
        assert (await outbox.maintain(db))["requeued"] == 1
        fresh = await outbox.claim(db, "w-new")
    assert fresh is not None
    assert fresh.lease_version > stale.lease_version
    async with penv.db() as db:
        assert not await outbox.delivered(db, stale)
        assert not await outbox.retry(db, stale, datetime.now(UTC), "network")
        assert not await outbox.give_up(db, stale, "network")
    row = await penv.row("SELECT leased_by, state, last_error FROM player_events")
    assert (row.leased_by, row.state, row.last_error) == ("w-new", "pending", None)
    async with penv.db() as db:
        assert await outbox.delivered(db, fresh)
    assert await rows(penv) == []


async def test_a_worker_that_outlived_its_lease_changes_nothing(penv: Env) -> None:
    """Another worker takes the lease while the request is out: the answer is not recorded."""
    user_id = await player_user(penv)
    await delete(penv, user_id)
    player = await fake_player(penv)

    async def taken_over_meanwhile(request: httpx.Request) -> httpx.Response:
        await penv.run(
            "UPDATE player_events SET leased_by = 'w2', lease_version = lease_version + 1"
        )
        return player.handler(request)

    def client() -> httpx.AsyncClient:
        return player_test_client(httpx.MockTransport(taken_over_meanwhile))

    result = await deliver_once(penv.factory, auth.load_event_signer(penv.settings), "w1",
                                client_factory=client)  # fmt: skip
    assert result is not None
    assert result.outcome == "lost"
    row = await penv.row("SELECT leased_by, state, attempt FROM player_events")
    assert (row.leased_by, row.state, row.attempt) == ("w2", "pending", 1)


async def test_after_7_days_the_event_fails_visibly_and_goes_30_days_later(penv: Env) -> None:
    user_id = await player_user(penv)
    await delete(penv, user_id)
    await penv.run(
        "UPDATE player_events SET created_at = now() - interval '8 days', "
        "give_up_at = now() - interval '1 day'"
    )
    player = await fake_player(penv)
    assert await deliver(penv, player) is None  # past give_up_at: never claimed
    async with penv.db() as db:
        counts = await outbox.maintain(db)
        assert counts["failed"] == 1
        assert counts["purged"] == 0
        failed, truncated = await outbox.failed_events(db)
    assert player.received == []
    assert not truncated
    [event] = failed
    assert (event.kind, event.attempts, event.last_error) == ("account_deleted", 0, "expired")
    assert not hasattr(event, "sub")
    assert not hasattr(event, "jti")
    await penv.run(
        "UPDATE player_events SET created_at = now() - interval '38 days', "
        "give_up_at = now() - interval '31 days'"
    )
    async with penv.db() as db:
        assert (await outbox.maintain(db))["purged"] == 1
    assert await rows(penv) == []


async def test_the_last_failure_within_the_7_days_gives_up(penv: Env) -> None:
    user_id = await player_user(penv)
    await delete(penv, user_id)
    await penv.run("UPDATE player_events SET give_up_at = now() + interval '1 minute'")
    player = await fake_player(penv)
    player.statuses = [500]
    later = datetime.now(UTC) + timedelta(minutes=2)
    result = await deliver(penv, player, now=lambda: later)
    assert (result.outcome, result.error) == ("failed", "status_5xx")
    row = await penv.row("SELECT state, leased_by, last_error FROM player_events")
    assert (row.state, row.leased_by, row.last_error) == ("failed", None, "status_5xx")


async def test_the_40th_attempt_is_the_last(penv: Env) -> None:
    user_id = await player_user(penv)
    await delete(penv, user_id)
    await penv.run("UPDATE player_events SET attempt = 39")
    player = await fake_player(penv)
    player.statuses = [502]
    result = await deliver(penv, player)
    assert (result.outcome, result.error) == ("failed", "status_5xx")
    assert await penv.scalar("SELECT attempt FROM player_events") == 40
    assert await deliver(penv, player) is None


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (httpx.Response(302, headers={"location": "https://player.example/x"}), "redirect"),
        (httpx.Response(302, headers={"location": "https://evil.example/x"}), "redirect"),
        (httpx.Response(200, headers={"content-length": "999999"}), "too_large"),
        (httpx.Response(404), "status_4xx"),
        (httpx.Response(101), "status_other"),
    ],
)
async def test_each_failure_is_stored_as_its_category(
    penv: Env, answer: httpx.Response, error: str
) -> None:
    user_id = await player_user(penv)
    await delete(penv, user_id)
    signer = auth.load_event_signer(penv.settings)

    def client() -> httpx.AsyncClient:
        return player_test_client(httpx.MockTransport(lambda _: answer))

    result = await deliver_once(penv.factory, signer, "w1", client_factory=client)
    assert result is not None
    assert (result.outcome, result.error) == ("retry", error)
    assert await penv.scalar("SELECT last_error FROM player_events") == error


@pytest.mark.parametrize(
    ("raised", "error"),
    [
        (httpx.ConnectError("refused"), "network"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (TimeoutError(), "timeout"),
    ],
)
async def test_a_transport_failure_is_stored_as_its_category(
    penv: Env, raised: Exception, error: str
) -> None:
    user_id = await player_user(penv)
    await delete(penv, user_id)
    signer = auth.load_event_signer(penv.settings)

    def fail(_: httpx.Request) -> httpx.Response:
        raise raised

    def client() -> httpx.AsyncClient:
        return player_test_client(httpx.MockTransport(fail))

    result = await deliver_once(penv.factory, signer, "w1", client_factory=client)
    assert result is not None
    assert (result.outcome, result.error) == ("retry", error)


async def test_a_signer_that_fails_leaves_the_event_for_a_later_attempt(penv: Env) -> None:
    user_id = await player_user(penv)
    await delete(penv, user_id)

    class Broken:
        def logout_token(self, **_: Any) -> str:
            raise RuntimeError("bad key")

    player = await fake_player(penv)
    result = await deliver_once(
        penv.factory, Broken(), "w1", client_factory=player.client_factory()
    )
    assert result is not None
    assert (result.outcome, result.error) == ("retry", "unsigned")
    assert player.received == []
