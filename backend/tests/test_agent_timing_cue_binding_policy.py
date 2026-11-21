"""``timing_cue_binding_policy``, through ``precise_kind``: on a track that is not exact, every
number in a cue must be bound to a section-level use, or the cue is refused (an allowlist, ADR
0040 section 3). Pure, table-driven: every number from 1 to 100, as a word, digits and full-width
digits, crossed with count contexts in en, sv and da, is refused; crossed with the bound uses it
passes."""

import itertools

import pytest

from classbeat.agent.timing_cue_binding_policy import unbound
from classbeat.agent.timing_cue_number_model import AMBIGUOUS
from classbeat.agent.timing_cue_text_policy import precise_kind
from test_agent_timing_cue_number_model import FULL_WIDTH, LANGS, spell

# {n} is the number; {m} the number before it (a countdown) or after it (a count up).
COUNTS = {
    "en": (
        "{n}",
        "{n}!",
        "Go on {n}",
        "On {n}, go",
        "{n} to go",
        "Last {n}!",
        "{n} more!",
        "Sprint in {n}",
        "{n} bars",
        "{n} beats left",
        "{n} more seconds",
        "Count {n}",
        "Hold for {n}",
        "Zone 4, {n}, go",
        "{n} and go",
        "And {n}, and {m}, go!",
        "{n} {m} up",
        "Gear 3: {n}, {m}",
        "Climb {n} {m}",
        "{n}... {m}...",
        "{n} of them",
    ),
    "sv": (
        "{n}",
        "{n}!",
        "Kör på {n}",
        "På {n}, kör",
        "{n} kvar!",
        "Sista {n}!",
        "{n} till!",
        "Spurta om {n}",
        "{n} takter",
        "{n} slag kvar",
        "{n} sekunder",
        "Räkna {n}",
        "Håll i {n}",
        "Zon 4, {n}, kör",
        "{n} och kör",
        "Och {n}, och {m}, kör!",
        "{n} {m} upp",
        "Växel 3: {n}, {m}",
        "Klättra {n} {m}",
        "{n}... {m}...",
        "{n} stycken",
    ),
    "da": (
        "{n}",
        "{n}!",
        "Kør på {n}",
        "På {n}, kør",
        "{n} tilbage!",
        "Sidste {n}!",
        "{n} mere!",
        "Spurt om {n}",
        "{n} takter",
        "{n} slag tilbage",
        "{n} sekunder",
        "Tæl {n}",
        "Hold i {n}",
        "Zone 4, {n}, kør",
        "{n} og kør",
        "Og {n}, og {m}, kør!",
        "{n} {m} op",
        "Gear 3: {n}, {m}",
        "Klatr {n} {m}",
        "{n}... {m}...",
        "{n} styk",
    ),
}
# The contexts in which an ambiguous word ("one", "en", "to", ...) reads as a number.
AMBIGUOUS_COUNTS = {0, 1, 2, 3, 8, 10, 13, 15, 16, 17, 18, 19}
BOUND = {
    "en": (
        "Zone {n}",
        "{n} minutes easy",
        "{n} rpm",
        "{n} %",
        "{n} watts",
        "{n} x 1 minute",
        "Climb {n} of 101",
        "Gear {n}, seated",
    ),
    "sv": (
        "Zon {n}",
        "{n} minuter lugnt",
        "{n} rpm",
        "{n} procent",
        "{n} watt",
        "{n} x 1 minut",
        "Klättring {n} av 101",
        "Växel {n}, sittande",
    ),
    "da": (
        "Zone {n}",
        "{n} minutter roligt",
        "{n} rpm",
        "{n} procent",
        "{n} watt",
        "{n} x 1 minut",
        "Klatring {n} af 101",
        "Gear {n}, siddende",
    ),
}


def _forms(lang: str, n: int) -> list[str]:
    return [spell(lang, n), str(n), str(n).translate(FULL_WIDTH)]


def _counts(lang: str) -> list[str]:
    rows = []
    for n, (i, context) in itertools.product(range(1, 101), enumerate(COUNTS[lang])):
        for k, form in enumerate(_forms(lang, n)):
            if k == 0 and form in AMBIGUOUS and i not in AMBIGUOUS_COUNTS:
                continue
            m = _forms(lang, n - 1 if n > 1 else 2)[k]
            rows.append(context.format(n=form, m=m))
    return rows


def _bound(lang: str) -> list[str]:
    return [
        context.format(n=form)
        for n, context in itertools.product(range(1, 101), BOUND[lang])
        for form in _forms(lang, n)
    ]


@pytest.mark.parametrize("lang", LANGS)
def test_every_number_in_a_count_context_is_refused(lang: str) -> None:
    assert [text for text in _counts(lang) if precise_kind(text) is None] == []


@pytest.mark.parametrize("lang", LANGS)
def test_every_number_in_a_bound_use_passes(lang: str) -> None:
    assert [text for text in _bound(lang) if precise_kind(text) is not None] == []


# The phrasings of the third review of #1323, each refused.
REVIEWED = [
    "And three, and two, and one, go!",
    "3 and 2 and 1 go",
    "Och tre, och två, och ett, kör!",
    "Og tre, og to, og en, kør!",
    "1 and 2 and 3 and 4",
    "Five more seconds",
    "5 more seconds!",
    "Ten more seconds",
    "Just 10 more seconds",
    "Ten to go",
    "Five to go!",
    "On three",
    "Go on three",
    "Go on 3",
    "Push on three",
    "On 3, go",
    "At the count of three",
    "Count of four",
    "On my count",
    "På tre",
    "På tre: kör",
    "Kör på tre",
    "Räkna till fyra",
    "Kør på tre",
    "Tæl til fire",
    "Tjugofem sekunder kvar",
    "Femogtyve sekunder tilbage",
    "Three, two\u2026",
    "3... 2...",
    "Tre... två...",
    "3; 2; 1",
    "5/4/3/2/1",
    "3 | 2 | 1",
    "3 \u2022 2 \u2022 1",
    "3 \u00b7 2 \u00b7 1",
    "3 \u2192 2 \u2192 1",
    "3 > 2 > 1",
    "3\u30012\u30011",
    "\uff13 \uff12 \uff11",
    "Song 1, 2 to go",
    "Twenty-five seconds left",
    "Tjugoen, tjugo",
    "Enogtyve sekunder",
    "Sixty seconds",
    "Zone 1-2-3-4",
    "Gear 3: 1, 2, 3, 4",
    "Zone 4 - 3, 2, 1",
    "Last 3 2 1 minutes",
    "2 more minutes",
    "1 minute left",
    "To minutter tilbage",
    "To, en, kør!",
    "On one!",
    "Ett, kör!",
    "Hold 0:10",
    "Rest 10 s",
    "2 x 30 s",
]
# Found by the fuzz self-check: an ambiguous word in a count.
OWN = [
    "Now: one more up!",
    "Pres - fire mere, spurt!",
    "Runde 4, tæl en nu",
    "Stand, hold one...",
    "OK to gange til afsted",
    "Last one!",
    "Sista ett!",
    "Zone one, two, three",
]
# The fourth review of #1323: counts made of bound numbers, and countdown words after a length.
COUNTED = [
    "Zone 3, zone 2, zone 1, go!",
    "Gear 3, gear 2, gear 1, sprint!",
    "Rep 3, rep 2, rep 1, go",
    "Step 1, step 2, step 3, step 4",
    "Zon 3, zon 2, zon 1, kör",
    "Runde 1, runde 2, runde 3",
    "3x, 2x, 1x, go",
    "3%, 2%, 1%",
    "1st, 2nd, 3rd, go!",
    "3rd, 2nd, 1st, go!",
    "3 gears, 2 gears, 1 gear",
    "3 minutes, 2 minutes, 1 minute",
    "2 minutes to go",
    "1:00 to go",
    "1:00 more",
    "2 minuter till",
    "And one!",
    "Och ett!",
    "Og et!",
]
# Section-level cues refused on purpose: seconds, counts of things and label lists.
REFUSED_ON_PURPOSE = [
    "Zone 3, 4, 5",
    "Zones 5 4 3",
    "Gears 4, 3, 2",
    "Level 3 2 1",
    "Rounds 1 2 3",
    "30 s all out",
    "Hold 45 seconds",
    "Three climbs today",
    "two more sprints",
    "in 2 sets",
    "Två spurter kvar",
    "Tre spurter, 30 s var",
    "Fem intervaller",
    "Tre spurter, 30 s hver",
    "in one go",
    "Three sprints",
    "Hold 0:45",
]


@pytest.mark.parametrize("text", REVIEWED + OWN + COUNTED + REFUSED_ON_PURPOSE)
def test_a_reviewed_or_ambiguous_phrasing_is_refused(text: str) -> None:
    assert precise_kind(text) is not None


NATURAL = {
    "en": [
        "Zone 4, hold",
        "Zone 3-4, steady",
        "Zone 2 to 3",
        "Back to zone 3",
        "Gear 3 to 4",
        "90-100 rpm, zone 4",
        "Sit and spin at 90 rpm",
        "Climb in 2 minutes",
        "2 to 3 minutes hard",
        "Last 5 minutes",
        "Zone 5 for 1 minute",
        "1.5 minutes easy",
        "Hold 1:30 standing",
        "Sprint 2 of 4",
        "Round 3 of 5, push",
        "4 x 1 minute hard",
        "60 % of your FTP",
        "200 watts, seated",
        "RPE 7",
        "Cadence 95",
        "One gear heavier",
        "Two gears up",
        "One more climb",
        "This one is long",
        "1st climb of the night",
        "Hits from 1985",
        "Drop!",
        "Drop to zone 2",
        "drop your cadence",
        "Legs on fire!",
        "Fire up the legs",
        "Ride the beat",
        "Find your rhythm",
        "Big chorus, push",
        "Easy spin, recover",
        "Level 2, seated",
        "Interval 3: zone 5",
        "Set 2, standing",
        "Step 4, climb",
        "Rep 2",
    ],
    "sv": [
        "Zon 4, håll",
        "Zon 3-4, jämnt",
        "Zon 2 till 3",
        "Tillbaka till zon 3",
        "Växel 3 till 4",
        "90-100 rpm, zon 4",
        "Sitt och trampa i 90 rpm",
        "Klättra i 2 minuter",
        "2 till 3 minuter hårt",
        "Sista 5 minuterna",
        "Zon 5 i 1 minut",
        "1,5 minuter lugnt",
        "Håll 1:30 stående",
        "Klättring 2 av 4",
        "Omgång 3 av 5, tryck",
        "4 x 1 minut hårt",
        "60 procent av FTP",
        "200 watt, sittande",
        "Kadens 95",
        "En växel tyngre",
        "Två växlar upp",
        "En sista klättring",
        "Ett tag till",
        "Om ett tag",
        "Ni är grymma",
        "1:a klättringen",
        "Låtar från 1985",
        "Släpp till zon 2",
        "Hitta rytmen",
        "Följ takten",
        "Stor refräng, tryck",
        "Lugnt, återhämta",
        "Nivå 2, sittande",
        "Intervall 3: zon 5",
        "Steg 4, klättra",
        "En gång till",
        "Stå upp och klättra",
        "Andas",
        "Tunga ben",
        "Le",
    ],
    "da": [
        "Zone 4, hold",
        "Zone 3-4, jævnt",
        "Zone 2 til 3",
        "Tilbage til zone 3",
        "Gear 3 til 4",
        "90-100 rpm, zone 4",
        "Sid og træd i 90 rpm",
        "Klatr i 2 minutter",
        "2 til 3 minutter hårdt",
        "Sidste 5 minutter",
        "Zone 5 i 1 minut",
        "1,5 minutter roligt",
        "Hold 1:30 stående",
        "Klatring 2 af 4",
        "Runde 3 af 5, pres",
        "4 x 1 minut hårdt",
        "60 procent af FTP",
        "200 watt, siddende",
        "Kadence 95",
        "Et gear tungere",
        "To gear op",
        "To minutter roligt",
        "En sidste klatring",
        "Et øjeblik",
        "Sange fra 1985",
        "Ned til zone 2",
        "Find rytmen",
        "Følg takten",
        "Stort omkvæd, pres",
        "Roligt, restitution",
        "Niveau 2, siddende",
        "Interval 3: zone 5",
        "Trin 4, klatr",
        "En gang til",
        "Stå op og klatr",
        "Træk vejret",
        "Tunge ben",
        "Smil",
        "To spurter tilbage",
        "Fire ben, hold",
    ],
}


@pytest.mark.parametrize(("lang", "text"), [(lang, t) for lang in LANGS for t in NATURAL[lang]])
def test_a_natural_section_level_cue_passes(lang: str, text: str) -> None:
    assert precise_kind(text) is None


def test_each_language_has_enough_rows() -> None:
    for lang in LANGS:
        assert len(_counts(lang)) >= 1000
        assert len(_bound(lang)) + len(NATURAL[lang]) >= 150


@pytest.mark.parametrize(
    ("text", "left"),
    [
        ("zone 4, 3, 2, 1", ["3", "2", "1"]),
        ("zone 3-4-5", ["5"]),  # one range, not a chain
        ("90-100 rpm", []),
        ("4-3 minutes", ["4"]),  # a range ascends
        ("2 of 4", []),
        ("1:30, 0:30", ["0:30"]),
        ("2 minutes left", ["2"]),
        ("in 1985", []),
        ("back to one leg", []),
        ("to, en", ["to", "en"]),
        ("niveau to-3", []),  # a label's number, then a range
        ("one more climb", []),  # the word, not a number
        ("to tråd", []),  # known gap: an ambiguous word counting things reads as the word
        ("2 minutes to go", ["2"]),
        ("1:00 to go", ["1:00"]),
        ("zone 3, zone 2, zone 1", ["3", "2", "1"]),
        ("zone 3, zone 2", []),  # two labels are a change, not a count
        ("zone 2, zone 4, zone 5", []),
        ("hold kadencen på 90", []),
        ("cadence at 95", []),
        ("zone 2, 3 minutes, zone 4", []),  # different uses are not a count
        ("runde 4, på ni og kør", ["ni"]),
        ("the one, go", ["one"]),
    ],
)
def test_unbound_lists_the_refused_numbers(text: str, left: list[str]) -> None:
    assert unbound(text) == left


# Ordinary section-level cues (the corpus of the fourth review of #1323, less "Zone 5 for 30
# seconds", refused on purpose): none is refused, so the correction loop is not starved.
ORDINARY = [
    "Seated climb, zone 3",
    "Stand up and climb, zone 4",
    "Add a gear, find 80 rpm",
    "Sprint! Zone 5, all out",
    "Recover in zone 2",
    "Two minutes seated, then stand",
    "Build through the chorus",
    "Hands on the bars, stay seated",
    "Ride with the beat",
    "Follow the music, push the pace",
    "Big push into the chorus",
    "Breathe, roll the legs out",
    "Add two gears and stand",
    "Back down to zone 2, easy spin",
    "Hold 85-95 rpm",
    "Interval 2 of 4: zone 5",
    "Final climb, give it everything",
    "Drop a gear and recover",
    "Drop to zone 2",
    "Jumps, up and down with the music",
    "Halfway there, keep going",
    "Last climb of the class",
    "One last push!",
    "Last song, make it count",
    "Sittande klättring, zon 3",
    "Stå upp, lägg på två växlar",
    "Spurta! Allt du har",
    "Sista klättringen, ge allt",
    "Återhämtning i zon 2, lätta ben",
    "Två minuter sittande",
    "Siddende bakke, zone 3",
    "Rejs dig, læg et gear på",
    "Spurt! Alt hvad du har",
    "Sidste bakke, giv alt",
    "Restitution i zone 2",
    "Hold kadencen på 90",
    "To minutter siddende",
    "En sidste indsats!",
    "Push for one minute",
    "Halfway through, one more interval",
    "Feel the bass, ride it",
    "Stand for the whole chorus",
    "Climb till the end of the song",
    "Sprint to the finish",
    "Ett sista ryck!",
    "Et sidste skub!",
    "Sprint 1 of 3 — zone 5",
    "Pick it up to 110 rpm",
    "Recover for 1 minute",
]


@pytest.mark.parametrize("text", ORDINARY)
def test_an_ordinary_cue_passes(text: str) -> None:
    assert precise_kind(text) is None
