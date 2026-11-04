"""Retry classes, backoff and the kind registry (ADR 0005 sections 2 and 4); no database."""

import importlib.util
import random
from datetime import UTC, datetime, timedelta
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
from classbeat.jobs.sentences import job_sentence


def migration_categories() -> set[str]:
    versions = Path(__file__).resolve().parent.parent / "migrations" / "versions"
    path = versions / "0029_beat_one_corrections.py"
    spec = importlib.util.spec_from_file_location("migration_0029", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {c.strip().strip("'") for c in module.CATEGORIES.split(",")}


def test_the_categories_match_the_migration_check() -> None:
    assert set(CATEGORIES) == migration_categories()
    # ``pool_missing`` (OP1, ADR 0008 section 1): the pool is forgotten and the retry creates anew.
    assert (
        frozenset({"upstream_unavailable", "rate_limited", "model_unavailable", "pool_missing"})
        == RETRYABLE
    )


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
        # ADR 0009 section 8's categories that are job categories keep their name (OP1).
        (ConnectionFailure("forbidden"), "forbidden", None),
        (ConnectionFailure("not_found"), "not_found", None),
        (ConnectionFailure("invalid_grant"), "invalid_grant", None),
        (ConnectionFailure("app_misconfigured"), "app_misconfigured", None),
        (ConnectionFailure("quota_exhausted"), "quota_exhausted", None),
        (ConnectionFailure("invalid_response"), "invalid_response", None),
        (ConnectionFailure("not_allowlisted"), "internal", None),  # connect only, never a job
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


def test_the_worker_ships_exactly_the_reviewed_kinds() -> None:
    import classbeat.design  # design_draft (D1), revise_draft (R1), refit_draft (BC3)
    import classbeat.saving
    import classbeat.songs  # design_with_songs (SJ1) and revise_songs (SJ3)
    import classbeat.swedebeat_handoff  # swedebeat_verify_mix (B2)
    import classbeat.swedebeat_match  # swedebeat_match (B1)

    assert classbeat.design.KIND
    assert classbeat.songs.KIND
    assert classbeat.swedebeat_match.KIND
    assert classbeat.swedebeat_handoff.KIND
    assert classbeat.saving.__name__
    from classbeat.jobs import KINDS

    assert KINDS.names() == [
        "design_draft",
        "design_with_songs",
        "refit_draft",  # ADR 0007 amendment "beat-one corrections" (BC3)
        "revise_draft",
        "revise_songs",
        "save_workout",
        "swedebeat_match",
        "swedebeat_verify_mix",
        "update_workout",
    ]


_NOW = datetime(2026, 11, 2, tzinfo=UTC)
_STOPPED = "Swedebeat asks to slow down. This match has stopped."
_HOUR = "Swedebeat asks to slow down; trying again in about 60 minutes."


def _catalogue_sentence(
    state: str, category: str | None, retry_after: int | None, wait: int
) -> str | None:
    return job_sentence(
        state,
        category,
        "rate_limited",
        retry_after,
        _NOW + timedelta(seconds=wait),
        _NOW,
        "swedebeat_match",
    )


def test_a_failed_catalogue_match_does_not_promise_another_try() -> None:
    message = _catalogue_sentence("failed", "rate_limited", 90, 0)
    assert message == _STOPPED
    assert "trying" not in _STOPPED
    assert _catalogue_sentence("failed", "rate_limited", 86_400, 0) == _STOPPED
    assert "Spotify" not in _STOPPED


def test_a_queued_catalogue_wait_names_at_most_the_pause_cap() -> None:
    short = _catalogue_sentence("queued", None, 90, 90)
    assert short == "Swedebeat asks to slow down; trying again in about 2 minutes."
    assert _catalogue_sentence("queued", None, 7_200, int(RATE_LIMIT_CAP_SECONDS)) == _HOUR
    assert _catalogue_sentence("queued", None, 86_400, int(RATE_LIMIT_CAP_SECONDS)) == _HOUR
    # No stored hint: the remaining pause is still named within the cap.
    assert _catalogue_sentence("queued", None, None, 7_200) == _HOUR
