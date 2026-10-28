"""The error mapping of ADR 0004 section 5: type and ``__cause__`` only, no chained exception."""

import email.message
import logging
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from classbeat.connections import ConnectionFailure, classify, run_library_call
from classbeat.egress import OutboundBlocked

TICKET = "LT-7f3a9c2e-ticket-from-the-sign-in"
PASSWORD = "correct horse battery staple"
TOKEN = "11111111-2222-3333-4444-555555555555"


class FakeMowlError(RuntimeError):
    """Stands in for ``mowl.errors.MowlError`` (the wheel is pinned in #66)."""


def http_error(status: int, retry_after: str | None = None) -> HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    # The URL carries the login ticket, as MOWL's sign-in does.
    return HTTPError(f"https://api.mowl.example/login/{TICKET}", status, TICKET, headers, None)


def mowl_error(cause: BaseException | None) -> FakeMowlError:
    error = FakeMowlError(f"sign-in failed for ticket {TICKET}: {TOKEN}")
    error.__cause__ = cause
    return error


CASES: list[tuple[str, Callable[[], BaseException], bool, str]] = [
    ("connect 401", lambda: mowl_error(http_error(401)), True, "invalid_credentials"),
    ("connect 403", lambda: mowl_error(http_error(403)), True, "invalid_credentials"),
    ("connect refusal", lambda: mowl_error(None), True, "invalid_credentials"),
    ("use 401", lambda: mowl_error(http_error(401)), False, "needs_reconnect"),
    ("use 403", lambda: mowl_error(http_error(403)), False, "needs_reconnect"),
    ("use no cause", lambda: mowl_error(None), False, "unknown"),
    ("429", lambda: mowl_error(http_error(429, "30")), False, "rate_limited"),
    ("500", lambda: mowl_error(http_error(500)), False, "upstream_unavailable"),
    ("503 connect", lambda: mowl_error(http_error(503)), True, "upstream_unavailable"),
    ("418", lambda: mowl_error(http_error(418)), False, "unknown"),
    ("404", lambda: mowl_error(http_error(404)), True, "unknown"),
    ("bare HTTPError 401", lambda: http_error(401), False, "needs_reconnect"),
    ("bare HTTPError 502", lambda: http_error(502), False, "upstream_unavailable"),
    ("bare HTTPError 400", lambda: http_error(400), True, "unknown"),
    ("URLError", lambda: URLError(f"no route to {TICKET}"), False, "upstream_unavailable"),
    ("TimeoutError", lambda: TimeoutError(TICKET), True, "upstream_unavailable"),
    ("socket.timeout", lambda: TimeoutError(TICKET), False, "upstream_unavailable"),
    ("ConnectionResetError", lambda: ConnectionResetError(TICKET), False, "upstream_unavailable"),
    ("ValueError", lambda: ValueError(TICKET), False, "unknown"),
    ("KeyError", lambda: KeyError(TICKET), True, "unknown"),
]


@pytest.mark.parametrize(("name", "make", "sign_in", "category"), CASES, ids=[c[0] for c in CASES])
def test_the_category_comes_from_type_and_cause(
    name: str, make: Callable[[], BaseException], sign_in: bool, category: str
) -> None:
    outcome = classify(make(), library_error=FakeMowlError, sign_in=sign_in)
    assert outcome.category == category
    assert TICKET not in repr(outcome)


def test_the_message_text_is_never_read() -> None:
    misleading = FakeMowlError("HTTP Error 401: Unauthorized (status 401, 429, 503)")
    assert classify(misleading, library_error=FakeMowlError, sign_in=False).category == "unknown"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("30", 30),
        (" 7 ", 7),
        ("0", 0),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),
        ("1.5", None),
        ("-3", None),
        ("", None),
        ("9999999", None),
        ("٣", None),
        (None, None),
        ("99999", 86_400),
    ],
)
def test_retry_after_is_an_integer_only(header: str | None, expected: int | None) -> None:
    outcome = classify(
        mowl_error(http_error(429, header)), library_error=FakeMowlError, sign_in=False
    )
    assert (outcome.category, outcome.retry_after, outcome.http_status) == (
        "rate_limited",
        expected,
        429,
    )


def test_classification_never_raises() -> None:
    class Hostile(FakeMowlError):
        @property
        def __cause__(self) -> Any:  # type: ignore[override]
            raise RuntimeError(TICKET)

    outcome = classify(Hostile(), library_error=FakeMowlError, sign_in=False)
    assert outcome.category == "unknown"


def raises(error: BaseException) -> Callable[[], Any]:
    def call() -> Any:
        password = PASSWORD  # held in the frame, as the sign-in's frame holds it
        assert password
        raise error

    return call


@pytest.mark.parametrize(("name", "make", "sign_in", "category"), CASES, ids=[c[0] for c in CASES])
async def test_a_failing_call_raises_a_bare_connection_failure(
    name: str,
    make: Callable[[], BaseException],
    sign_in: bool,
    category: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ConnectionFailure) as raised:
        await run_library_call(raises(make()), library_error=FakeMowlError, sign_in=sign_in)
    failure = raised.value
    assert failure.args == (category,)
    assert failure.category == category
    assert failure.__cause__ is None
    assert failure.__context__ is None
    assert failure.__suppress_context__ is False
    for text in (str(failure), repr(failure), caplog.text):
        for secret in (TICKET, PASSWORD, TOKEN, "api.mowl.example"):
            assert secret not in text
    record = caplog.records[-1]
    assert record.__dict__["category"] == category  # the category, class name and status only


async def test_retry_after_reaches_the_failure() -> None:
    with pytest.raises(ConnectionFailure) as raised:
        await run_library_call(
            raises(mowl_error(http_error(429, "12"))), library_error=FakeMowlError, sign_in=False
        )
    assert (raised.value.args, raised.value.retry_after) == (("rate_limited",), 12)


async def test_a_successful_call_returns_its_value_from_a_worker_thread() -> None:
    import threading

    main = threading.get_ident()
    result = await run_library_call(
        lambda: threading.get_ident(), library_error=FakeMowlError, sign_in=True
    )
    assert result != main


def test_an_unknown_category_becomes_unknown() -> None:
    assert ConnectionFailure("made-up").args == ("unknown",)


async def test_an_allowlist_refusal_stays_outbound_blocked_without_a_chain(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ADR 0006 §9: not a connection category; host only, no chain, no URL."""

    def call() -> None:
        raise OutboundBlocked("evil.example")

    with caplog.at_level(logging.WARNING), pytest.raises(OutboundBlocked) as raised:
        await run_library_call(call, library_error=FakeMowlError, sign_in=False)
    assert raised.value.host == "evil.example"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert not [r for r in caplog.records if r.getMessage() == "provider call failed"]


async def test_an_allowlist_refusal_wrapped_by_the_library_is_still_outbound_blocked() -> None:
    def call() -> None:
        error = FakeMowlError(f"GET v1/x failed: {TOKEN}")
        error.__cause__ = OutboundBlocked("evil.example")
        raise error

    with pytest.raises(OutboundBlocked) as raised:
        await run_library_call(call, library_error=FakeMowlError, sign_in=False)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert TOKEN not in str(raised.value)
