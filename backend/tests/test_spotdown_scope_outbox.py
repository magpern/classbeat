"""ADR 0040 section 7, threat model T133 (#1301 part 3): account deletion deletes the owner's
spotdown scope. The web app records it in ``spotdown_scope_deletions`` (the scope only, an HMAC of
the owner id, never the id) and the worker's maintenance sends ``DELETE /v2/scopes/{scope}`` to the
fake spotdown, once, then forgets it. Never the network.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, text

from authtools import Env, running_env
from classbeat.account import erase
from classbeat.spotdown import scope_outbox
from classbeat.spotdown.scope_calc import cache_scope
from classbeat.spotdown.scope_key import scope_key
from ctx import make_context
from jobtools import new_user
from spotdownfake import SERVICE_KEY, FakeSpotdown, spotdown

__all__ = ["spotdown"]  # the fixture

KEY = bytes(range(32, 64))


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url) as running:
        yield running


@pytest.fixture
def admin_sql(env: Env, superuser: Connection) -> Any:
    def run(statement: str, **params: Any) -> Any:
        result = superuser.execute(text(statement), params)
        return result.all() if result.returns_rows else None

    return run


async def test_the_scope_is_recorded_never_the_id_and_only_once(env: Env, admin_sql: Any) -> None:
    owner = uuid4()
    await scope_outbox.record_for(env.factory, KEY, owner)
    await scope_outbox.record_for(env.factory, KEY, owner)
    found = admin_sql("SELECT scope, attempts FROM spotdown_scope_deletions")
    assert [(r.scope, r.attempts) for r in found] == [(cache_scope(KEY, owner), 0)]
    assert str(owner) not in found[0].scope
    assert owner.hex not in found[0].scope


async def test_the_worker_deletes_the_scope_in_spotdown_and_forgets_it(
    env: Env, admin_sql: Any, spotdown: FakeSpotdown
) -> None:
    owner, other = uuid4(), uuid4()
    mine, theirs = cache_scope(KEY, owner), cache_scope(KEY, other)
    spotdown.add_analysis(mine, "sha256:" + "a" * 64)
    kept = spotdown.add_analysis(theirs, "sha256:" + "a" * 64)
    await scope_outbox.record_for(env.factory, KEY, owner)
    assert await scope_outbox.drain(env.factory) == 1
    deletes = [s for s in spotdown.seen if s.method == "DELETE"]
    assert [d.path for d in deletes] == [f"/v2/scopes/{mine}"]
    assert deletes[0].headers["authorization"] == f"Bearer {SERVICE_KEY}"
    assert list(spotdown.analyses) == [kept]  # the other owner's scope is untouched
    assert admin_sql("SELECT scope FROM spotdown_scope_deletions") == []
    assert await scope_outbox.drain(env.factory) == 0  # nothing left to send


async def test_a_failed_delete_is_kept_and_retried_later_not_at_once(
    env: Env, admin_sql: Any, spotdown: FakeSpotdown
) -> None:
    await scope_outbox.record_for(env.factory, KEY, uuid4())
    spotdown.connect_error = True
    assert await scope_outbox.drain(env.factory) == 0
    (row,) = admin_sql(
        "SELECT attempts, next_attempt_at > now() + interval '50 seconds' AS later "
        "FROM spotdown_scope_deletions"
    )
    assert (row.attempts, row.later) == (1, True)
    spotdown.connect_error = False
    assert await scope_outbox.drain(env.factory) == 0  # not due yet: nothing is sent
    assert len([s for s in spotdown.seen if s.method == "DELETE"]) == 1
    admin_sql("UPDATE spotdown_scope_deletions SET next_attempt_at = now()")
    assert await scope_outbox.drain(env.factory) == 1


async def test_without_spotdown_nothing_is_sent_and_only_stale_rows_go(
    env: Env, admin_sql: Any
) -> None:
    fresh, stale = uuid4(), uuid4()
    await scope_outbox.record_for(env.factory, KEY, fresh)
    await scope_outbox.record_for(env.factory, KEY, stale)
    admin_sql(
        "UPDATE spotdown_scope_deletions SET created_at = now() - interval '31 days' "
        "WHERE scope = :s",
        s=cache_scope(KEY, stale),
    )
    assert await scope_outbox.drain(env.factory) == 1
    left = admin_sql("SELECT scope FROM spotdown_scope_deletions")
    assert [r.scope for r in left] == [cache_scope(KEY, fresh)]


async def test_the_users_delete_records_its_scope_in_the_same_transaction(
    env: Env, admin_sql: Any, superuser: Connection
) -> None:
    owner: UUID = new_user(superuser)
    assert await erase.delete_row(env.factory, make_context(owner), KEY)
    found = admin_sql("SELECT scope FROM spotdown_scope_deletions")
    assert [r.scope for r in found] == [cache_scope(KEY, owner)]
    # Nothing to delete: nothing is recorded.
    assert not await erase.delete_row(env.factory, make_context(uuid4()), KEY)
    assert len(admin_sql("SELECT scope FROM spotdown_scope_deletions")) == 1


async def test_the_operators_delete_records_the_scope_through_the_job_ender(
    env: Env, admin_sql: Any, superuser: Connection
) -> None:
    from classbeat.jobs import UnmeteredLedger

    owner = new_user(superuser)
    end = erase.make_job_ender(env.factory, UnmeteredLedger(), KEY)
    await end(owner)
    found = admin_sql("SELECT scope FROM spotdown_scope_deletions")
    assert [r.scope for r in found] == [cache_scope(KEY, owner)]


def test_the_web_app_and_the_worker_derive_the_same_key(env: Env) -> None:
    settings = env.app.state.settings
    key = scope_key(settings)
    assert key is not None
    assert len(key) == 32
    assert scope_key(settings.model_copy(update={"app_secret_key": None})) is None
