"""``classbeat.class_title_policy`` (issue #1233; pure): the title cleaner and the interpreter's
short name for a new class, and how ``InterpretedRequest.sanitised`` keeps it."""

import unicodedata

import pytest
from pydantic import ValidationError

from classbeat.agent.interpreted import InterpretedRequest
from classbeat.class_title_policy import NAME_MAX, TITLE_MAX, clean_title, proposed_name
from classbeat.classes import duplicate_request


def test_clean_title_is_the_duplicates_cleaner() -> None:
    assert duplicate_request.clean_title is clean_title
    assert clean_title(" Hills\n\tand\u200b  hits ") == "Hills and hits"
    assert clean_title("x" * TITLE_MAX) == "x" * TITLE_MAX
    assert clean_title("x" * (TITLE_MAX + 1)) is None
    assert clean_title(" \u0007 ") is None


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("Three hills and a flat tempo", "Three hills and a flat tempo"),
        ('"Three hills and a flat tempo"', "Three hills and a flat tempo"),
        ("\u201cTre backar och ett tempo\u201d", "Tre backar och ett tempo"),
        ("\u00abTre bakker\u00bb", "Tre bakker"),
        ("Hill \u201cfever\u201d ride", "Hill fever ride"),  # double quotes inside go too
        ("Don\u2019t stop climbing", "Don\u2019t stop climbing"),  # an apostrophe stays
        ("Rock 'n' roll ride", "Rock 'n' roll ride"),
        ("Fire ride \U0001f525\U0001f6b4\u200d\u2640\ufe0f", "Fire ride"),  # emoji, ZWJ, VS16
        ("Line\nbreak\tride", "Line break ride"),
        ("x" * NAME_MAX, "x" * NAME_MAX),
    ],
)
def test_a_name_is_cleaned_as_a_title(raw: str, kept: str) -> None:
    assert proposed_name(raw) == kept


@pytest.mark.parametrize(
    "raw", [None, "", "   ", '""', "\U0001f525\U0001f525", " - ", "x" * (NAME_MAX + 1)]
)
def test_a_name_with_nothing_left_or_too_long_is_none(raw: str | None) -> None:
    assert proposed_name(raw) is None


def test_the_reading_keeps_a_cleaned_name_or_none() -> None:
    read = InterpretedRequest.model_validate({"name": '"Hills and hits" \U0001f525'})
    assert read.sanitised("hills", 0).name == "Hills and hits"
    assert InterpretedRequest.model_validate({"name": "\U0001f525"}).sanitised("x", 0).name is None
    assert InterpretedRequest.model_validate({}).sanitised("x", 0).name is None
    assert "name" not in {r.path for r in read.readings()}  # never a brief value


def test_the_schema_refuses_only_a_name_that_is_not_text() -> None:
    with pytest.raises(ValidationError):
        InterpretedRequest.model_validate({"name": 7})


@pytest.mark.parametrize("name", ["", "x" * (NAME_MAX + 1), "x" * 5000])
def test_an_empty_or_long_name_is_dropped_never_a_correction_round(name: str) -> None:
    # #1237: the bound applies after the cleaning, so the schema takes it and ``sanitised`` drops
    # it; a revise, which ignores the name, never asks for a correction over it.
    read = InterpretedRequest.model_validate({"name": name})
    assert read.sanitised("x", 0).name is None


def test_the_bound_applies_after_the_cleaning() -> None:
    padded = '"' + "x" * NAME_MAX + '" \U0001f525\U0001f525'  # over 60 only by quotes and emoji
    assert len(padded) > NAME_MAX
    assert proposed_name(padded) == "x" * NAME_MAX
    read = InterpretedRequest.model_validate({"name": padded})
    assert read.sanitised("x", 0).name == "x" * NAME_MAX


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("\u3164\u3164", None),  # Hangul fillers alone look blank
        ("\u115f\u1160 \uffa0", None),
        ("Hills\u3164", "Hills"),
        ("Ride\U000e0100\U000e01ef", "Ride"),  # variation selectors supplement
        ("\u20e3", None),  # an orphan combining keycap
        ("1\ufe0f\u20e3 climb", "1 climb"),
    ],
)
def test_invisible_characters_are_dropped(raw: str, kept: str | None) -> None:
    assert proposed_name(raw) == kept


@pytest.mark.parametrize(
    "raw",
    [
        "https://pwned.example/win",
        "Ride HTTP://x",
        "Climb at www.example",
        "Mail coach@example",
        "evil.com hills",
        "Hills on Ride.se",
    ],
)
def test_a_link_like_name_is_dropped(raw: str) -> None:
    assert proposed_name(raw) is None


@pytest.mark.parametrize(
    "raw", ["St. Louis climb", "3.5 km of hills", "t.ex. backar", "bl.a. sprint"]
)
def test_a_dot_in_words_is_no_link(raw: str) -> None:
    assert proposed_name(raw) == raw


TRICKY = [
    "\"'Spin ride' \U0001f6b4\u200d\u2640\ufe0f\"",  # the review's case: a quote beside a ZWJ emoji
    "'Spin ride'\u200d",
    '\u200e"Hills"\u200f',  # bidi marks around quotes
    "\u202a\u201cTre backar\u201d\u202c",
    "\u2066'Tempo'\u2069 ride",
    "Ride '\u200b'",
    "\u3164'Hills'\u3164",  # fillers around quotes
    "'\u115f\u1160'",
    "1\ufe0f\u20e3 '2\ufe0f\u20e3' climbs",  # keycaps
    "\u20e3'Ride'\u20e3",
    "  \u00a0 'Spaced'\u2003 \t\n",  # spaces of every kind
    "\u00ab \u2039'x'\u203a \u00bb",
    "Don\u2019t stop\u2019",
    '"""',
    "'" + "x" * 60 + "'\u200d",
    "Rock 'n' roll\u00ad'",  # a soft hyphen (a format character) beside a quote
    "\ufeff'Ride'\ufeff",
]


@pytest.mark.parametrize("raw", TRICKY)
def test_the_cleaning_is_idempotent(raw: str) -> None:
    # #1237: the eval's backstop compares a kept name with its own cleaning.
    once = proposed_name(raw)
    assert proposed_name(once) == once
    if once is not None:
        assert once == once.strip("'\u2019\" ")
        assert not any(unicodedata.category(c)[0] == "C" for c in once)


def test_a_quote_beside_a_format_character_is_stripped() -> None:
    assert proposed_name(TRICKY[0]) == "Spin ride"
    assert proposed_name("'Spin ride'\u200d") == "Spin ride"


@pytest.mark.parametrize(
    "raw",
    [
        "'Hills'\x07",
        "\x07'Hills'",
        "\u201cHills\x07\u201d",
        "Hill\x07 \x0bclimbs",
        " \x07 ",
        "x\x07" * TITLE_MAX,
    ],
)
def test_cleaning_twice_equals_cleaning_once(raw: str) -> None:
    """A control character, even beside a quote, leaves nothing for a second pass to remove."""
    once_title = clean_title(raw)
    if once_title is not None:
        assert clean_title(once_title) == once_title
    once_name = proposed_name(raw)
    if once_name is not None:
        assert proposed_name(once_name) == once_name
        assert clean_title(once_name) == once_name


def test_a_quote_around_a_control_character_is_stripped_in_one_pass() -> None:
    assert proposed_name("'Hills'\x07") == "Hills"
    assert proposed_name("\x07'Hills'") == "Hills"
