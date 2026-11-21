"""``timing_cue_unit_policy``, through ``precise_kind``: a number, ordinal or article next to a bar,
beat or count word is refused, before any binding (ADR 0040 section 3). Pure, table-driven: every
unit word crossed with every number form (digits, digit ordinal, ordinal word, spelled number,
article, "N of M"), in front and behind, with and without a separator, in en, sv and da."""

import itertools

import pytest

from classbeat.agent.timing_cue_text_policy import precise_kind
from classbeat.agent.timing_cue_unit_policy import unit_count

# (unit words in front of which a number counts, unit words after which one counts)
UNITS = {
    "en": (
        ("bar", "bars", "beat", "beats", "count", "counts", "downbeat"),
        ("bar", "beat", "count"),
    ),
    "sv": (
        ("takt", "takter", "takten", "takterna", "slag", "slaget", "taktslag"),
        ("takt", "slag"),
    ),
    "da": (
        ("takt", "takter", "takten", "slag", "slaget", "tælling", "tællinger"),
        ("takt", "slag"),
    ),
}
# Number forms per language: numbers, digit ordinals, ordinal words, articles.
NUMBERS = {
    "en": ("8", "16", "eight", "one", "twenty-five", "fourty"),
    "sv": ("8", "16", "åtta", "ett", "tjugofem", "sexton"),
    "da": ("8", "16", "otte", "to", "femogtyve", "seksten"),
}
ORDINALS = {
    "en": (
        "8th",
        "1st",
        "2nd",
        "3rd",
        "eighth",
        "third",
        "first",
        "second",
        "twenty-first",
        "sixteenth",
    ),
    "sv": ("8:e", "1:a", "2:a", "åttonde", "tredje", "fjärde", "andra", "sextonde", "tjugonde"),
    "da": ("8.", "8:e", "ottende", "tredje", "fjerde", "anden", "sekstende", "tyvende"),
}
ARTICLES = {"en": ("a", "an", "one"), "sv": ("en", "ett"), "da": ("en", "et")}
GAP = {"en": "more", "sv": "korta", "da": "korte"}
OF = {"en": "of", "sv": "av", "da": "af"}
WRAPS = (("", ""), ("Stand, ", ""), ("", ", go!"), ("Sprint on the ", " now"))


def _precise(lang: str) -> list[str]:
    front, behind = UNITS[lang]
    core: list[str] = []
    for unit in front:
        for n in NUMBERS[lang] + ORDINALS[lang]:
            core += [f"{n} {unit}", f"{n}-{unit}"]
            if n not in {"en", "ett", "et"}:  # as articles they count only directly ("en takt")
                core.append(f"{n} {GAP[lang]} {unit}")
        core += [f"{a} {unit}" for a in ARTICLES[lang]]
        core += [f"7 {OF[lang]} 8 {unit}", f"the last 4 {OF[lang]} 8 {unit}"]
    for unit in behind:
        for n in NUMBERS[lang]:
            core += [f"{unit} {n}", f"{unit}: {n}", f"{unit}-{n}", f"{unit} {n} {OF[lang]} 9"]
        core += [f"{unit}8", f"{unit} #8"]
    return [f"{a}{c}{b}" for c, (a, b) in itertools.product(core, WRAPS)]


@pytest.mark.parametrize("lang", list(UNITS))
def test_every_number_form_next_to_a_unit_is_refused(lang: str) -> None:
    assert [text for text in _precise(lang) if precise_kind(text) is None] == []


def test_the_table_is_broad() -> None:
    assert all(len(_precise(lang)) >= 500 for lang in UNITS)


# The phrasings of the fourth review of #1323, each refused.
REVIEWED = [
    "Sprint on the 8th bar",
    "Stand on the 4th beat",
    "Go on the 1st beat of the chorus",
    "Stand on the 16th count",
    "Push from the 5th bar",
    "Spurta på 8:e takten",
    "Stå upp på 4:e slaget",
    "Kør på 8. takt",
    "On the third beat, stand",
    "On the fourth bar, sprint",
    "Stand on the eighth count",
    "On the first count, stand",
    "Sprint on the second beat",
    "Spurta på fjärde takten",
    "Stå upp på åttonde slaget",
    "Kør på fjerde takt",
    "Rejs dig på ottende slag",
    "On the sixteenth beat, go",
    "Bar 7 of 8, get ready",
    "Beat 3 of 4",
    "Count 7 of 8, sprint",
    "Stand for the last 4 of 8 counts",
    "Takt 7 av 8",
    "Takt 7 af 8",
    "Beat 3 of 4 now",
    "One more bar, then sprint",
    "Just one more bar!",
    "One more beat",
    "One more count, go",
    "Wait a bar, then go",
    "Hold for a bar, then sprint",
    "In a bar, stand",
    "Stand on bar one",
    "Sprint from bar one",
    "Stå upp på takt ett",
    "Kør på takt et",
    "Hit it at beat one",
    "Fourty more counts",
    "En takt till",
    "To takter mere",
    "Hundred beats",
    "Halv takt",
    "Half a bar",
    "Varannan takt",
    "Hver anden takt",
    "Every other bar",
    "On the and, stand",
    "Every second beat",
    "On the fiveth bar, go",
    "På nio:e takten",
]


@pytest.mark.parametrize("text", REVIEWED)
def test_a_reviewed_position_cue_is_refused(text: str) -> None:
    assert precise_kind(text) is not None


ALLOWED = [
    "Hands on the bars, stay seated",
    "Raise the bar in the chorus",
    "Follow the beat",
    "Ride with the beat",
    "Feel the beat",
    "Make it count",
    "Last song, make it count",
    "Håll en jämn takt",
    "Hold en jævn takt",
    "Följ takten",
    "Håll takten nu",
    "Hitta takten",
    "Følg takten",
    "Hold takten",
    "Find takten",
    "I takt med musiken",
    "Takten är tung",
    "Barbell arms",
    "Beatles hit",
    "Countryside climb",
    "Bars down, sit",
]


@pytest.mark.parametrize("text", ALLOWED)
def test_a_unit_word_without_a_number_passes(text: str) -> None:
    assert not unit_count(text.casefold())
