"""The pure rules of the player events (ADR 0034 section 2): the pinned claims, the backoff,
the 40-attempt and 7-day caps, and the error categories."""

from datetime import UTC, datetime, timedelta

import pytest

from classbeat.player.event_policy import (
    BACKCHANNEL_LOGOUT,
    GIVE_UP_AFTER,
    LONGEST_DELAY,
    MAX_ATTEMPTS,
    GiveUp,
    Retry,
    after_failure,
    logout_claims,
    retry_delay,
    status_category,
)

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("kind", "urn"),
    [
        ("account_deleted", "urn:classbeat:player:account-deleted"),
        ("sessions_revoked", "urn:classbeat:player:sessions-revoked"),
    ],
)
def test_the_claims_are_the_pinned_logout_token(kind: str, urn: str) -> None:
    claims = logout_claims(
        issuer="https://classbeat.example",
        client_id="classbeat-player",
        subject="s" * 43,
        jti="j" * 43,
        kind=kind,  # pyright: ignore[reportArgumentType]
        now=NOW,
    )
    iat = int(NOW.timestamp())
    assert claims == {
        "iss": "https://classbeat.example",
        "aud": "classbeat-player",
        "iat": iat,
        "exp": iat + 300,
        "jti": "j" * 43,
        "sub": "s" * 43,
        "events": {BACKCHANNEL_LOGOUT: {}, urn: {}},
    }
    assert BACKCHANNEL_LOGOUT == "http://schemas.openid.net/event/backchannel-logout"
    assert "nonce" not in claims
    assert "sid" not in claims


@pytest.mark.parametrize(
    ("attempt", "seconds"),
    [(1, 30), (2, 60), (3, 120), (5, 480), (10, 15360), (11, 21600), (40, 21600), (99, 21600)],
)
def test_the_delay_doubles_from_30_seconds_to_6_hours(attempt: int, seconds: int) -> None:
    assert retry_delay(attempt, 0.0) == timedelta(seconds=seconds)


def test_the_jitter_is_ten_percent_either_way() -> None:
    assert retry_delay(1, 1.0) == timedelta(seconds=33)
    assert retry_delay(1, -1.0) == timedelta(seconds=27)
    assert retry_delay(40, 1.0) == LONGEST_DELAY * 1.1


@pytest.mark.parametrize(("attempt", "jitter"), [(0, 0.0), (-1, 0.0), (1, 1.01), (1, -1.5)])
def test_a_bad_attempt_or_jitter_is_refused(attempt: int, jitter: float) -> None:
    with pytest.raises(ValueError, match=r"attempts count from 1|jitter is between"):
        retry_delay(attempt, jitter)


def test_a_failure_retries_but_never_after_give_up_at() -> None:
    give_up_at = NOW + GIVE_UP_AFTER
    assert after_failure(attempt=1, now=NOW, give_up_at=give_up_at, jitter=0.0) == Retry(
        NOW + timedelta(seconds=30)
    )
    late = give_up_at - timedelta(minutes=1)
    assert after_failure(attempt=20, now=late, give_up_at=give_up_at, jitter=0.0) == Retry(
        give_up_at
    )


@pytest.mark.parametrize(
    ("attempt", "now_offset"),
    [(MAX_ATTEMPTS, timedelta(0)), (MAX_ATTEMPTS + 1, timedelta(0)), (1, GIVE_UP_AFTER)],
)
def test_the_40th_attempt_or_7_days_give_up(attempt: int, now_offset: timedelta) -> None:
    give_up_at = NOW + GIVE_UP_AFTER
    result = after_failure(attempt=attempt, now=NOW + now_offset, give_up_at=give_up_at, jitter=0)
    assert result == GiveUp()


def test_40_attempts_fit_within_the_7_days() -> None:
    total = sum((retry_delay(n, 1.0) for n in range(1, MAX_ATTEMPTS)), timedelta())
    assert total > GIVE_UP_AFTER  # so give_up_at, not the count, usually ends a dead player


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (200, None),
        (204, None),
        (299, None),
        (301, "redirect"),
        (399, "redirect"),
        (400, "status_4xx"),
        (403, "status_4xx"),
        (499, "status_4xx"),
        (500, "status_5xx"),
        (503, "status_5xx"),
        (100, "status_other"),
        (600, "status_other"),
    ],
)
def test_the_status_categories(status: int, category: str | None) -> None:
    assert status_category(status) == category
