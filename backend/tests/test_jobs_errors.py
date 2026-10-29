"""Retry classes, backoff and the kind registry (ADR 0005 sections 2 and 4); no database."""

import importlib.util
import random
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import DisconnectionError, OperationalError

from classbeat.connections import ConnectionFailure
from classbeat.egress import OutboundBlocked
from classbeat.jobs import (
    CATEGORIES,
    RETRYABLE,
    Complete,
    JobFailure,
    JobKind,
    KindRegistry,
    backoff_seconds,
    failure_of,
)
from classbeat.jobs.errors import BACKOFF_CAP_SECONDS, RATE_LIMIT_CAP_SECONDS


def migration_categories() -> set[str]:
    path = Path(__file__).resolve().parent.parent / "migrations" / "versions" / "0005_classes.py"
    spec = importlib.util.spec_from_file_location("migration_0005", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {c.strip().strip("'") for c in module.CATEGORIES.split(",")}


def test_the_categories_match_the_migration_check() -> None:
    assert set(CATEGORIES) == migration_categories()
    assert frozenset({"upstream_unavailable", "rate_limited", "model_unavailable"}) == RETRYABLE


@pytest.mark.parametrize("attempt", [1, 2, 3, 4, 5, 6, 10])
def test_backoff_doubles_from_30_seconds_with_jitter_up_to_15_minutes(attempt: int) -> None:
    base = min(30 * 2 ** (attempt - 1), BACKOFF_CAP_SECONDS)
    for seed in range(50):
        delay = backoff_seconds(attempt, rng=random.Random(seed))  # noqa: S311
        assert base * 0.8 <= delay <= base * 1.2


def test_a_rate_limit_waits_at_least_its_hint_capped_at_an_hour() -> None:
    assert backoff_seconds(1, 600) >= 600
    assert backoff_seconds(1, 86_400) == RATE_LIMIT_CAP_SECONDS
    assert backoff_seconds(1, 0) <= 36


@pytest.mark.parametrize(
    ("raised", "category", "retry_after"),
    [
        (ConnectionFailure("rate_limited", 7), "rate_limited", 7),
        (ConnectionFailure("upstream_unavailable"), "upstream_unavailable", None),
        (ConnectionFailure("invalid_credentials"), "needs_reconnect", None),
        (ConnectionFailure("key_unavailable"), "key_unavailable", None),
        (ConnectionFailure("decrypt_failed"), "decrypt_failed", None),
        (ConnectionFailure("not_connected"), "not_connected", None),
        (ConnectionFailure("unknown"), "internal", None),
        (OutboundBlocked("x.example"), "outbound_blocked", None),
        (JobFailure("model_unavailable"), "model_unavailable", None),
        (JobFailure("no such category"), "internal", None),
        (JobFailure("upstream_unavailable", 9), "upstream_unavailable", None),  # hint ignored
        (KeyError("secret"), "internal", None),
        # #109 item 3: our own database blinking is retryable wherever it happens.
        (OperationalError("SELECT 1", {}, ConnectionError()), "upstream_unavailable", None),
        (DisconnectionError("gone"), "upstream_unavailable", None),
    ],
)
def test_every_exception_maps_to_one_category(
    raised: BaseException, category: str, retry_after: int | None
) -> None:
    failure = failure_of(raised)
    assert (failure.category, failure.retry_after) == (category, retry_after)
    assert failure.args == (category,)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Loose(BaseModel):
    pass


async def _run(job_run: object) -> Complete:
    return Complete()


def test_the_registry_holds_valid_kinds_once() -> None:
    registry = KindRegistry()
    kind = registry.register(JobKind("test_kind", Input, ("pick",), _run))
    assert registry.get("test_kind") is kind
    assert registry.names() == ["test_kind"]
    with pytest.raises(ValueError, match="registered"):
        registry.register(JobKind("test_kind", Input, ("pick",), _run))
    registry.unregister("test_kind")
    assert registry.names() == []


@pytest.mark.parametrize(
    ("name", "model", "stages"),
    [
        ("Bad-Name", Input, ("pick",)),
        ("x" * 41, Input, ("pick",)),
        ("ok", Input, ()),
        ("ok", Input, ("Bad Stage",)),
        ("ok", Loose, ("pick",)),
    ],
)
def test_invalid_kinds_are_refused(
    name: str, model: type[BaseModel], stages: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="kind"):
        JobKind(name, model, stages, _run)


def test_the_worker_ships_exactly_the_design_and_revise_kinds() -> None:
    import classbeat.design  # noqa: F401 - registers design_draft, revise_draft
    from classbeat.jobs import KINDS

    assert KINDS.names() == ["design_draft", "revise_draft"]  # M3b adds save and update; M4 more
