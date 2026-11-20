"""``timing_cue_policy``: what a cue may say and where it may sit, per timing class and per kind
of instruction (ADR 0040 section 3). Pure, table-driven."""

import pytest

from classbeat.agent.timing_cue_policy import CUE_MARGIN_S, CueAt, precise_kind, problems

ANCHORS = (0.0, 60.0, 120.0)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("3, 2, 1, go!", "countdown"),
        ("5-4-3 and up", "countdown"),
        ("10, 9, 8 ...", "countdown"),
        ("3 2 1", "countdown"),
        ("Count down with me", "countdown"),
        ("Up in 5 seconds", "countdown"),
        ("Three, two, one", "countdown"),
        ("Nedräkning nu", "countdown"),
        ("Upp om 5 sekunder", "countdown"),
        ("Tæl ned", "countdown"),
        ("Push for 4 bars", "beat_or_bar"),
        ("Hold the 8-count", "beat_or_bar"),
        ("Stand on the downbeat", "beat_or_bar"),
        ("Go on beat one", "beat_or_bar"),
        ("Next bar, sprint", "beat_or_bar"),
        ("Håll i 8 takter", "beat_or_bar"),
        ("Upp på ettan", "beat_or_bar"),
        ("Næste takt", "beat_or_bar"),
        ("Sprint on the drop!", "drop_hit"),
        ("Wait for the drop", "drop_hit"),
        ("When the bass drops, go", "drop_hit"),
        ("The drop hits now", "drop_hit"),
        ("Spurta på droppen", "drop_hit"),
        ("Når droppet kommer", "drop_hit"),
        # Section-level wording, a cadence on the beat and plain numbers are not precise.
        ("Stand up and climb", None),
        ("Ride on the beat", None),
        ("Zone 3, 4, 5", None),
        ("90-100 rpm, zone 4", None),
        ("2 x 30 s", None),
        ("Raise the bar in the chorus", None),
        ("Big build in this section", None),
        ("", None),
    ],
)
def test_precise_kind(text: str, kind: str | None) -> None:
    assert precise_kind(text) == kind


def cue(cls: str, text: str, start: float = 60.0) -> CueAt:
    return CueAt(2, start, 0, cls, ANCHORS, text)  # pyright: ignore[reportArgumentType]


# One row per class and kind of instruction: (class, cue text, start, refused?).
TABLE = [
    # exact: everything is allowed, wherever it sits.
    ("exact", "3, 2, 1, go!", 60.0, False),
    ("exact", "Push for 4 bars", 91.0, False),
    ("exact", "Sprint on the drop!", 60.0, False),
    ("exact", "Stand up", 91.0, False),
    # approximate: no precise cue anywhere; a section-level cue only near an anchor.
    ("approximate", "3, 2, 1, go!", 60.0, True),
    ("approximate", "Push for 4 bars", 60.0, True),
    ("approximate", "Sprint on the drop!", 60.0, True),
    ("approximate", "Stand up", 60.0, False),
    ("approximate", "Stand up", 60.0 + CUE_MARGIN_S, False),
    ("approximate", "Stand up", 60.0 - CUE_MARGIN_S, False),
    ("approximate", "Stand up", 60.0 + CUE_MARGIN_S + 0.5, True),
    ("approximate", "Stand up", 91.0, True),
    ("approximate", "", 91.0, False),  # no cue, nothing to refuse
    # unusable: the same, the strictest.
    ("unusable", "3, 2, 1, go!", 60.0, True),
    ("unusable", "Push for 4 bars", 60.0, True),
    ("unusable", "Sprint on the drop!", 60.0, True),
    ("unusable", "Stand up", 60.0, False),
    ("unusable", "Stand up", 91.0, True),
    ("unusable", "", 91.0, False),
]


@pytest.mark.parametrize(("cls", "text", "start", "refused"), TABLE)
def test_the_table(cls: str, text: str, start: float, refused: bool) -> None:
    found = problems([cue(cls, text, start)])
    assert bool(found) is refused
    if refused:
        assert "interval 2" in found[0]
        assert "track 0" in found[0]
        assert cls in found[0]
        assert "leave it empty" in found[0]  # says how to fix it


def test_a_precise_cue_names_its_kind() -> None:
    (countdown,) = problems([cue("approximate", "3, 2, 1")])
    (drop,) = problems([cue("approximate", "On the drop")])
    assert "a countdown" in countdown
    assert "a drop hit" in drop


def test_every_refused_cue_is_reported() -> None:
    cues = [cue("approximate", "3, 2, 1"), cue("unusable", "Stand", 91.0), cue("exact", "3 2 1")]
    assert len(problems(cues)) == 2
