"""The rules of an invite request's text (ADR 0033 section 2), pure."""

import pytest

from classbeat.auth.invite_request_policy import HEARD_FROM_MAX, clean_heard_from


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("", ""),
        ("   ", ""),
        ("  Instagram  ", "Instagram"),
        ("a\nb\r\nc\td", "a b c d"),
        ("Spinning-klubben i Malmö", "Spinning-klubben i Malmö"),
        ("<b>html</b> & stuff", "<b>html</b> & stuff"),  # text, never markup: kept as typed
        ("x" * HEARD_FROM_MAX, "x" * HEARD_FROM_MAX),
        ("  " + "x" * HEARD_FROM_MAX + "  ", "x" * HEARD_FROM_MAX),
    ],
)
def test_text_is_kept_with_its_whitespace_folded(given: str, stored: str) -> None:
    assert clean_heard_from(given) == stored


@pytest.mark.parametrize(
    "given",
    [
        "x" * (HEARD_FROM_MAX + 1),
        "nul\x00",
        "bell\x07",
        "c1\x9bx",
        "rtl \u202e override",
        "zero\u200bwidth",
        "bom\ufeff",
        "private \ue000",
        "unassigned \U000e0080",
    ],
)
def test_control_format_and_overlong_text_is_refused(given: str) -> None:
    with pytest.raises(ValueError, match="heard_from"):
        clean_heard_from(given)
