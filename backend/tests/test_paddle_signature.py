"""The Paddle-Signature check (ADR 0032 section 1, #972): table-driven, no I/O."""

import hashlib
import hmac
from pathlib import Path

import pytest

from classbeat.billing.paddle_signature import (
    DEFAULT_WINDOW_S,
    MAX_HEADER_CHARS,
    MAX_SIGNATURES,
    Rejected,
    Verified,
    sign,
    verify,
)

FIXTURE = Path(__file__).parent / "fixtures" / "paddle" / "transaction_completed_web.json"
BODY = FIXTURE.read_bytes()
SECRET = "test-webhook-secret-one"  # a test value, not a Paddle key
OTHER = "test-webhook-secret-two"
TS = 1_780_000_000


def h1(secret: str, ts: int = TS, body: bytes = BODY) -> str:
    """Computed independently of the module, from Paddle's documented scheme."""
    return hmac.new(secret.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()


GOOD = h1(SECRET)
WRONG = h1(OTHER)


def test_sign_matches_paddles_documented_scheme() -> None:
    assert sign(BODY, SECRET, ts=TS) == f"ts={TS};h1={GOOD}"


def test_window_is_five_minutes() -> None:
    assert DEFAULT_WINDOW_S == 300


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (f"ts={TS};h1={GOOD}", Verified(TS)),
        (f"h1={GOOD};ts={TS}", Verified(TS)),  # order of the parts does not matter
        (f"ts={TS}; h1={GOOD}", Verified(TS)),  # spaces around a part are allowed
        (f"ts={TS};h1={GOOD.upper()}", Verified(TS)),  # hex case does not matter
        (f"ts={TS};h1={WRONG};h1={GOOD}", Verified(TS)),  # rotation: any h1 may match
        (f"ts={TS};h1={GOOD};h1={WRONG}", Verified(TS)),
        (None, Rejected("missing")),
        ("", Rejected("missing")),
        (f"ts={TS};h1={WRONG}", Rejected("mismatch")),
        (f"ts={TS};h1={'0' * 64}", Rejected("mismatch")),
        (f"ts={TS + 1};h1={GOOD}", Rejected("mismatch")),  # ts is part of what is signed
        (f"ts={TS}", Rejected("malformed")),  # no h1
        (f"h1={GOOD}", Rejected("malformed")),  # no ts
        (f"ts={TS};ts={TS};h1={GOOD}", Rejected("malformed")),  # two ts
        (f"ts={TS};h1={GOOD};v0=abc", Rejected("malformed")),  # nothing else is allowed
        (f"ts={TS};h1={GOOD};", Rejected("malformed")),  # an empty part
        (f"ts={TS},h1={GOOD}", Rejected("malformed")),
        (f"ts=-{TS};h1={GOOD}", Rejected("malformed")),
        (f"ts={TS}.0;h1={GOOD}", Rejected("malformed")),
        (f"ts= {TS};h1={GOOD}", Rejected("malformed")),
        (f"ts={TS};h1={GOOD[:-1]}", Rejected("malformed")),  # 63 hex digits
        (f"ts={TS};h1={GOOD}0", Rejected("malformed")),  # 65 hex digits
        (f"ts={TS};h1={'g' * 64}", Rejected("malformed")),
        (f"ts={TS};H1={GOOD}", Rejected("malformed")),  # keys are case-sensitive
        (
            f"ts={TS};" + ";".join([f"h1={WRONG}"] * MAX_SIGNATURES + [f"h1={GOOD}"]),
            Rejected("malformed"),
        ),
        (f"ts={TS};h1={GOOD};" + "x" * MAX_HEADER_CHARS, Rejected("malformed")),
    ],
)
def test_header(header: str | None, expected: Verified | Rejected) -> None:
    assert verify(header, BODY, [SECRET], now=TS) == expected


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (0, Verified(TS)),
        (300, Verified(TS)),  # exactly five minutes old
        (-300, Verified(TS)),  # exactly five minutes ahead
        (301, Rejected("outside_window")),  # a replay of an old delivery
        (-301, Rejected("outside_window")),  # a timestamp from the future
        (86_400, Rejected("outside_window")),
    ],
)
def test_replay_window(offset: int, expected: Verified | Rejected) -> None:
    assert verify(f"ts={TS};h1={GOOD}", BODY, [SECRET], now=TS + offset) == expected


def test_window_is_checked_before_the_signature() -> None:
    # An old, wrongly signed header says only that it is old: no HMAC work for a stale header.
    assert verify(f"ts={TS};h1={WRONG}", BODY, [SECRET], now=TS + 999) == Rejected("outside_window")


def test_custom_window() -> None:
    header = f"ts={TS};h1={GOOD}"
    assert verify(header, BODY, [SECRET], now=TS + 10, window_s=5) == Rejected("outside_window")
    assert verify(header, BODY, [SECRET], now=TS + 5, window_s=5) == Verified(TS)


@pytest.mark.parametrize(
    ("secrets", "expected"),
    [
        ([SECRET], Verified(TS)),
        ([OTHER, SECRET], Verified(TS)),  # rotation: the new and the old secret
        ([SECRET, OTHER], Verified(TS)),
        ([OTHER], Rejected("mismatch")),
    ],
)
def test_secret_rotation(secrets: list[str], expected: Verified | Rejected) -> None:
    assert verify(f"ts={TS};h1={GOOD}", BODY, secrets, now=TS) == expected


@pytest.mark.parametrize(
    "body",
    [
        BODY + b" ",  # a re-serialized body would not match
        BODY.replace(b'"1000"', b'"1"'),
        BODY.replace(b"transaction.completed", b"transaction.completeD"),
        b"",
    ],
)
def test_any_change_to_the_raw_body_is_a_mismatch(body: bytes) -> None:
    assert verify(f"ts={TS};h1={GOOD}", body, [SECRET], now=TS) == Rejected("mismatch")


@pytest.mark.parametrize("secrets", [[], [""], [SECRET, ""], [SECRET] * 5])
def test_bad_secret_configuration_raises(secrets: list[str]) -> None:
    with pytest.raises(ValueError, match="secrets"):
        verify(f"ts={TS};h1={GOOD}", BODY, secrets, now=TS)


def test_empty_secret_cannot_sign() -> None:
    with pytest.raises(ValueError, match="secrets"):
        sign(BODY, "", ts=TS)


def test_negative_window_raises() -> None:
    with pytest.raises(ValueError, match="window_s"):
        verify(f"ts={TS};h1={GOOD}", BODY, [SECRET], now=TS, window_s=-1)


def test_results_carry_no_secret_body_or_signature() -> None:
    results = [
        verify(f"ts={TS};h1={GOOD}", BODY, [SECRET], now=TS),
        verify(f"ts={TS};h1={WRONG}", BODY, [SECRET], now=TS),
    ]
    for result in results:
        shown = repr(result)
        assert SECRET not in shown
        assert GOOD not in shown
        assert WRONG not in shown
        assert "transaction" not in shown


def test_comparisons_are_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every h1 is compared with `hmac.compare_digest`, with every secret, with no early exit."""
    import classbeat.billing.paddle_signature as module

    pairs: list[tuple[str, str]] = []
    real = hmac.compare_digest

    def spy(a: str, b: str) -> bool:
        pairs.append((a, b))
        return real(a, b)

    monkeypatch.setattr(module.hmac, "compare_digest", spy)
    header = f"ts={TS};h1={GOOD};h1={WRONG}"
    assert verify(header, BODY, [SECRET, OTHER], now=TS) == Verified(TS)
    assert len(pairs) == 4
