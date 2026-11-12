"""``db.statement_policy`` (#1006): the allowlist ``system_statement`` checks before it runs.

Refused: anything but one ``INSERT``, ``UPDATE`` or ``DELETE`` on a system table built from that
table's own columns, bind parameters and the listed functions and operators. The probes below are
the review's of #1009 (multi-row ``VALUES``, prefixes, hints, a ``values()`` construct that made the
earlier blocklist walk loop forever) and the earlier blocklist's own cases. Accepted: every
statement the rate limiter sends today, captured from a real run.
"""

import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    Table,
    bindparam,
    column,
    delete,
    exists,
    func,
    lateral,
    literal,
    literal_column,
    select,
    table,
    text,
    tuple_,
    union,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql.elements import Grouping, Label

from authtools import Env, running_env
from classbeat.auth import ratelimit
from classbeat.auth.tables import rate_limits, sessions
from classbeat.db import session as session_module
from classbeat.db.statement_policy import REFUSED, check_system_write

W = datetime(2026, 1, 1, tzinfo=UTC)
L = rate_limits.c
OWNED = Table("classes", MetaData(), Column("n", Integer))
ROW = {"bucket_hash": b"x", "window_start": W}
UPSERT_KEYS = [L.bucket_hash, L.window_start]


def _refused() -> dict[str, object]:
    owned = select(OWNED.c.n).scalar_subquery()
    taken = delete(OWNED).returning(OWNED.c.n).cte("taken")
    v = values(column("n", Integer), name="v").data([(1,)])
    return {
        # B1 of the review: multi-row VALUES, prefixes and hints were never walked.
        "multi values, owned subquery": insert(rate_limits).values([{**ROW, "count": owned}]),
        "multi values, text": insert(rate_limits).values([{**ROW, "count": text("1")}]),
        "multi values, second row": insert(rate_limits).values(
            [{**ROW, "count": 1}, {**ROW, "count": owned}]
        ),
        "prefix ONLY": update(rate_limits).prefix_with("ONLY").values(count=1),
        "prefix comment": insert(rate_limits).values(**ROW, count=0).prefix_with("/*x*/"),
        "hint": update(rate_limits).values(count=1).with_hint("x"),
        # B2: a values() construct (its columns' table yields its own columns again).
        "values construct": update(rate_limits).where(L.count.in_(select(v.c.n))).values(count=1),
        "values construct, whole": update(rate_limits)
        .where(L.count.in_(select(v)))
        .values(count=1),
        # Subqueries and selectables, of system tables too.
        "system-table subquery": insert(rate_limits).values(
            **ROW, count=select(func.count(sessions.c.id)).scalar_subquery()
        ),
        "alias of the target": update(rate_limits)
        .where(L.count.in_(select(rate_limits.alias("r").c.count)))
        .values(count=1),
        "subquery column": update(rate_limits)
        .where(L.count.in_(select(select(L.count).subquery().c.count)))
        .values(count=1),
        "table-valued function": update(rate_limits)
        .where(L.count.in_(select(func.generate_series(1, 3).table_valued("g").c.g)))
        .values(count=1),
        "exists": delete(rate_limits).where(exists().where(OWNED.c.n == 1)),
        "union": delete(rate_limits).where(L.count.in_(union(select(L.count), select(OWNED.c.n)))),
        "lateral": delete(rate_limits).where(L.count.in_(select(lateral(select(OWNED.c.n)).c.n))),
        "textual select": delete(rate_limits).where(
            L.count.in_(text("SELECT n FROM classes").columns(column("n", Integer)))
        ),
        "tuple": update(rate_limits).where(tuple_(L.count) == tuple_(OWNED.c.n)).values(count=1),
        "insert from select": insert(rate_limits).from_select(["count"], select(L.count)),
        "data-modifying cte": update(rate_limits)
        .where(L.count.in_(select(taken.c.n)))
        .values(count=1),
        "independent cte": update(rate_limits).values(count=1).add_cte(taken),
        # Other tables and columns.
        "owned table in values": insert(rate_limits).values(**ROW, count=owned),
        "owned column (implicit FROM)": update(rate_limits)
        .where(L.count == OWNED.c.n)
        .values(count=1),
        "owned column of an alias": update(rate_limits)
        .where(L.count == OWNED.alias("o").c.n)
        .values(count=1),
        "bare column()": update(rate_limits).where(column("n") == 1).values(count=1),
        "returning a table": insert(rate_limits).values(**ROW, count=0).returning(OWNED),
        "returning an owned subquery": insert(rate_limits).values(**ROW, count=0).returning(owned),
        "lightweight owned table": update(table("classes", column("n"))).values(n=1),
        "lightweight system table": update(table("rate_limits", column("count"))).values(count=1),
        "system name in another schema": update(
            Table("rate_limits", MetaData(), Column("count", Integer), schema="other")
        ).values(count=1),
        "owned target": OWNED.insert().values(n=1),
        # Opaque SQL and functions off the list.
        "text in returning": insert(rate_limits).values(**ROW, count=0).returning(text("1")),
        "text in where": delete(rate_limits).where(text("true")),
        "text in set": update(rate_limits).values(count=text("1")),
        "literal column": insert(rate_limits)
        .values(**ROW, count=0)
        .returning(literal_column("(SELECT n FROM classes)")),
        "set_config": update(rate_limits).values(
            count=func.length(func.set_config("app.user_id", "x", False))
        ),
        "count(*)": update(rate_limits).values(count=func.count()),
        "cast": update(rate_limits).values(count=func.cast(literal(1), Integer)),
        "schema-qualified function": update(rate_limits).values(count=func.pg_catalog.greatest(1)),
        "in": delete(rate_limits).where(L.count.in_([1, 2])),
        "or": delete(rate_limits).where((L.count == 1) | (L.count == 2)),
        # ON CONFLICT: the target's columns and DO UPDATE SET only.
        "on conflict where": insert(rate_limits)
        .values(**ROW, count=1)
        .on_conflict_do_update(
            index_elements=UPSERT_KEYS, set_={"count": 1}, where=exists().where(OWNED.c.n == 1)
        ),
        "on conflict set text": insert(rate_limits)
        .values(**ROW, count=1)
        .on_conflict_do_update(
            index_elements=["bucket_hash", "window_start"], set_={"count": text("1")}
        ),
        "on conflict index_where": insert(rate_limits)
        .values(**ROW, count=1)
        .on_conflict_do_nothing(index_elements=[L.bucket_hash], index_where=text("true")),
        "on conflict, owned column": insert(rate_limits)
        .values(**ROW, count=1)
        .on_conflict_do_nothing(index_elements=[OWNED.c.n]),
        "on conflict constraint": insert(rate_limits)
        .values(**ROW, count=1)
        .on_conflict_do_nothing(constraint="rate_limits_pkey"),
        "a select": select(L.count),
        "text statement": text("DELETE FROM sessions"),
    }


@pytest.mark.parametrize("name", sorted(_refused()))
def test_it_refuses_everything_off_the_allowlist(name: str) -> None:
    with pytest.raises(ValueError, match=REFUSED):
        check_system_write(_refused()[name])


def test_it_allows_bind_parameters_and_the_upsert() -> None:
    check_system_write(update(rate_limits).values(count=bindparam("c")))
    check_system_write(update(rate_limits).values(count=literal(1)))
    check_system_write(
        insert(rate_limits)
        .values([{**ROW, "count": 1}, {**ROW, "count": 2}])
        .on_conflict_do_update(index_elements=UPSERT_KEYS, set_={"count": L.count + 1})
        .returning(L.count.label("hits"), func.clock_timestamp().label("now"))
    )


def _finishes(statement: object) -> None:
    """Runs the check in a thread: it must end (refuse or allow) within seconds, never loop."""
    outcome: list[str] = []

    def check() -> None:
        try:
            check_system_write(statement)
            outcome.append("allowed")
        except ValueError:
            outcome.append("refused")

    worker = threading.Thread(target=check, daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "the walk did not end"
    assert outcome


def test_the_walk_ends_on_a_self_referencing_construct() -> None:
    """A cycle of allowed elements (a label that is its own grouping's element) ends the walk:
    every element is visited once. The ``values()`` construct of the review ends too."""
    label = Label("x", L.count)
    grouping = Grouping(label)
    label._element = grouping  # pyright: ignore[reportAttributeAccessIssue]
    label.element = grouping  # pyright: ignore[reportAttributeAccessIssue]
    _finishes(update(rate_limits).values(count=grouping))
    v = values(column("n", Integer), name="v").data([(1,)])
    _finishes(update(rate_limits).where(L.count.in_(select(v.c.n))).values(count=1))


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url) as env:
        yield env


async def test_every_limiter_statement_is_accepted(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real windows (``_window_start`` unfrozen) and every write the limiter has: each one
    reaches the database through the check, and none is refused."""
    seen: list[str] = []
    real = session_module.check_system_write

    def record(statement: object) -> None:
        real(statement)
        seen.append(type(statement).__name__)

    monkeypatch.setattr(session_module, "check_system_write", record)
    limiter: ratelimit.RateLimiter = env.app.state.limiter
    ip = "203.0.113.9"
    attempt = await limiter.begin(
        ratelimit.password_buckets("a@example.com", ip, known_device=False)
    )
    await attempt.succeed()  # one delete (the pair), two decrements
    await limiter.begin_together(ratelimit.api_buckets(env.settings, "u1", ip, "POST", "/api/x"))
    start = ratelimit.spotify_start_buckets("u1", ip)
    await limiter.begin(start)
    await limiter.release(start)
    await limiter.start_cooldown("u1", 120)
    assert seen == ["Insert"] * 3 + ["Delete", "Update", "Update"] + ["Insert"] * 3 + [
        "Update"
    ] * 2 + ["Insert"]
