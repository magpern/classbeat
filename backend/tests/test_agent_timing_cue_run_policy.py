"""``timing_cue_run_policy``, through ``precise_kind``: a countdown or a count up is found wherever
it sits in a cue, also after a zone, gear, level or round number; a label's own number and a
duration in minutes are the only numbers left out (ADR 0040 section 3). Pure, table-driven: the
precise rows cross label prefixes and separators with count tails in en, sv and da."""

import itertools

import pytest

from classbeat.agent.timing_cue_run_policy import run_kind
from classbeat.agent.timing_cue_text_policy import precise_kind

C, B = "countdown", "beat_or_bar"
SEPARATORS = (" ", ": ", " - ", ". ", ", ", " \u2013 ")

# (label words, count-down tails, count-up tails) per language.
LANGUAGES = {
    "en": (
        ("Zone", "Gear", "Level", "Round", "Sprint", "Climb", "Interval", "Set"),
        (
            "3 2 1",
            "3, 2, 1, go!",
            "three, two, one",
            "5 4 3 2 1 up",
            "4-3-2-1 and go",
            "ten nine eight",
        ),
        ("1 2 3 4", "1, 2, 3, 4 and go", "one two three four", "5-6-7-8"),
    ),
    "sv": (
        ("Zon", "Växel", "Nivå", "Omgång", "Spurt", "Klättring", "Varv", "Steg"),
        (
            "3 2 1",
            "3, 2, 1, kör!",
            "tre, två, ett",
            "fem fyra tre två ett",
            "4-3-2-1 och upp",
            "tio nio åtta",
        ),
        ("1 2 3 4", "ett, två, tre, fyra och kör", "1-2-3", "fem sex sju åtta"),
    ),
    "da": (
        ("Zone", "Gear", "Niveau", "Runde", "Spurt", "Klatring", "Interval", "Trin"),
        (
            "3 2 1",
            "3, 2, 1, kør!",
            "tre, to, en",
            "fem fire tre to en",
            "4-3-2-1 og op",
            "ti ni otte",
        ),
        ("1 2 3 4", "en, to, tre, fire og kør", "1-2-3", "fem seks syv otte"),
    ),
}
PREFIX_NUMBERS = ("1", "2", "4", "5", "30")


def _precise(lang: str) -> list[tuple[str, str]]:
    labels, down, up = LANGUAGES[lang]
    rows = [(f"{tail}", C) for tail in down] + [(f"{tail}", B) for tail in up]
    for label, n, sep in itertools.product(labels, PREFIX_NUMBERS, SEPARATORS):
        rows += [(f"{label} {n}{sep}{tail}", C) for tail in down]
        rows += [(f"{label} {n}{sep}{tail}", B) for tail in up]
    return rows


# The phrasings of the review of #1323 (round 2), each refused.
REVIEWED = [
    ("Gear 4: 3 2 1 go", C),
    ("Zone 5 - 3, 2, 1, go!", C),
    ("Zone 4. 3, 2, 1, go!", C),
    ("Round 2 - 3, 2, 1, go!", C),
    ("Level 6, 3, 2, 1, sprint", C),
    ("Level 2: three, two, one, go", C),
    ("Stand, zone 4: 4 3 2 1 up!", C),
    ("Sprint 30 3 2 1 go", C),
    ("Climb 2 3 2 1", C),
    ("Zone 2 1 2 3 4", B),
    ("Gear 3: 1, 2, 3, 4 and go", B),
    ("Gear 4 countdown 3 2 1", C),
    ("Växel 4: 3 2 1 kör", C),
    ("Zon 5 - tre, två, ett, kör!", C),
    ("Nivå 2: tre två ett", C),
    ("Spurt 30 3 2 1 kör", C),
    ("Zon 2 1 2 3 4", B),
    ("Gear 4: 3 2 1 kør", C),
    ("Zone 5 - fem, fire, tre", C),
    ("Runde 2 - 3, 2, 1, kør!", C),
    ("Niveau 6, 3, 2, 1, spurt", C),
    ("Gear 3: 1, 2, 3, 4 og kør", B),
    # the label is not directly before the run, so none of its numbers is left out
    ("Zone 4, hold, 3 2 1", C),
    ("Zon 4 håll 3 2 1", C),
    ("Gear 2 sid ned 4 3 2", C),
]

# Section-level phrasings that read as a count and are refused on purpose: a label list of three
# or more steps, or one followed by a call, is not told apart from a countdown (the correction
# round rewords it).
REFUSED_ON_PURPOSE = [
    ("Zones 5 4 3 2", C),
    ("Level 4 3 2 1", C),
    ("Zone 3 2 1 go", C),
    ("Gears 4, 3, 2!", C),
    ("Zones 1 2 3 4", B),
    ("Zoner 5 4 3 2", C),
    ("Zon 3 2 1 kör", C),
    ("Zone 3 2 1 kør", C),
    ("Last 4 3 2 1 minutes", C),
]


@pytest.mark.parametrize("lang", LANGUAGES)
def test_a_count_after_any_prefix_is_refused(lang: str) -> None:
    missed = [(text, kind) for text, kind in _precise(lang) if precise_kind(text) != kind]
    assert missed == []


@pytest.mark.parametrize(("text", "kind"), REVIEWED + REFUSED_ON_PURPOSE)
def test_a_reviewed_or_ambiguous_phrasing_is_refused(text: str, kind: str) -> None:
    assert precise_kind(text) == kind


_TAILS = {
    "en": ("hold", "recover", "seated climb", "push the chorus", "easy spin", "back to zone 2"),
    "sv": (
        "håll",
        "återhämta",
        "sittande klättring",
        "tryck i refrängen",
        "lugnt",
        "tillbaka till zon 2",
    ),
    "da": (
        "hold",
        "restitution",
        "siddende klatring",
        "pres i omkvædet",
        "roligt",
        "tilbage til zone 2",
    ),
}
_LIST_TAILS = {"en": "recovery", "sv": "återhämtning", "da": "restitution"}
_PLURALS = {
    "en": ("Zones", "Gears", "Levels", "Rounds"),
    "sv": ("Zoner", "Växlar", "Nivåer", "Omgångar"),
    "da": ("Zoner", "Gearene", "Niveauer", "Runder"),
}
_PLURALS = {
    "en": ("Zones", "Gears", "Levels", "Rounds"),
    "sv": ("Zoner", "Växlar", "Nivåer", "Omgångar"),
    "da": ("Zoner", "Gearene", "Niveauer", "Runder"),
}


def _plain(lang: str) -> list[str]:
    labels = LANGUAGES[lang][0][:4]
    rows = [
        f"{label} {n}{sep}{tail}"
        for label, n, sep, tail in itertools.product(
            labels, ("1", "3", "5"), (" ", ", ", ": "), _TAILS[lang]
        )
    ]
    for (label, plural), n in itertools.product(
        zip(labels, _PLURALS[lang], strict=True), (3, 4, 5)
    ):
        rows += [
            f"{label} {n} {n - 1} {n - 2} {_LIST_TAILS[lang]}",
            f"{plural} {n}, {n - 1}, {n - 2}",
        ]
        rows += [f"{label} {n - 2} {n - 1} {n} {_LIST_TAILS[lang]}", f"{label} {n} {n - 1}"]
    return rows


PLAIN = {lang: _plain(lang) for lang in LANGUAGES}
MINUTES = [
    "Last 3 2 1 minutes",
    "3 2 1 minuter kvar",
    "sidste 3 2 1 minutter",
    "Rounds 1 2 3",
    "Zone 3, 4, 5",
]


@pytest.mark.parametrize("lang", LANGUAGES)
def test_a_label_number_is_not_a_count(lang: str) -> None:
    assert [text for text in PLAIN[lang] if precise_kind(text) is not None] == []


@pytest.mark.parametrize("text", MINUTES)
def test_minutes_are_not_a_count(text: str) -> None:
    assert precise_kind(text) is None


def test_each_language_has_a_hundred_plain_and_precise_rows() -> None:
    for lang in LANGUAGES:
        assert len(PLAIN[lang]) >= 100
        assert len(_precise(lang)) >= 100


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("3 2 1", C),
        ("1 2 3", B),
        ("3 2", None),
        ("1 3 5 7", None),
        ("2 1 2 1", None),
        ("9 3 2 1 9", C),
        ("zone 3 2 1", None),
        ("zone 3 2 1 go", C),
        ("zone 4 3 2 1", C),
        ("3 2 1 minutes", None),
        ("4 3 2 1 minutes", C),
    ],
)
def test_run_kind_reads_any_three_steps(text: str, kind: str | None) -> None:
    assert run_kind(text) == kind
