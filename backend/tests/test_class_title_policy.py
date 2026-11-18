"""``classbeat.class_title_policy`` (issue #1233; pure): the title cleaner and the interpreter's
short name for a new class, and how ``InterpretedRequest.sanitised`` keeps it."""

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


@pytest.mark.parametrize("name", ["", "x" * (NAME_MAX + 1), 7])
def test_the_schema_refuses_an_empty_long_or_non_text_name(name: object) -> None:
    with pytest.raises(ValidationError):
        InterpretedRequest.model_validate({"name": name})
