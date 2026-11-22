"""#1390 (ADR 0042 section 5, amending ADR 0036 A1 and A7): cut pins in ``mix_plan_policy.checked``.
A pinned cut skips the candidate selection (``bar``: snapped request; ``exact``: the request;
confirmed: exactly ``confirmed_sec``), every hard rule still refuses with a pin present, a spec
without a pin is byte-identical to before, and the table of mode x confirmed x reanalysis x
``music_changed`` (the effective cut, ``listen_again`` and the pins' hints). Pure: no I/O."""

import json
from dataclasses import replace
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.design.revision import studio_hash
from classbeat.studio import mix_hint_policy
from classbeat.studio.mix_facts_model import TrackFacts
from classbeat.studio.mix_pin_policy import listen_again
from classbeat.studio.mix_plan_policy import MixPlanRefused, checked
from classbeat.studio.mix_spec_model import MixSpec
from classbeat.studio.output_body_policy import Facts, playlist_body, render_plan
from mix_choice_fixtures import phrases

ORDER = ["a", "b", "c"]
OLD = "0" * 64  # the analysed file a pin was confirmed against
NEW = "1" * 64  # another file: music_changed
# 2 s bars from 0; trusted phrase points at 32, 52, 64 and 97.4 (off the grid on purpose)
A0 = TrackFacts(240.0, 120.0, 0.0, "exact", phrases(32.0, 52.0, 64.0, 97.4))
A1 = replace(A0, first_downbeat=0.12)  # a reanalysis: the grid moved by 0.12 s
PLAIN = TrackFacts(240.0, 120.0, 0.0, "exact")


def facts(a: TrackFacts = A0, **more: TrackFacts) -> dict[str, TrackFacts]:
    return {"a": a, "b": PLAIN, "c": PLAIN, **more}


def pin(requested: float, mode: str = "bar", confirmed: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"requested_sec": requested, "mode": mode, "confirmed": False}
    if confirmed is not None:
        out.update(confirmed=True, confirmed_sec=confirmed, confirmed_against=OLD)
    return out


def piece(reference: str, start: float, end: float, **pins: Any) -> dict[str, Any]:
    return {"reference": reference, "start_sec": start, "end_sec": end, **pins}


def mix(*pieces: dict[str, Any], crossfades: list[Any] | None = None) -> dict[str, Any]:
    fades = [0] * (len(pieces) - 1) if crossfades is None else crossfades
    return {"tracks": list(pieces), "crossfades": fades}


def first_a(end_pin: dict[str, Any] | None = None, end: float = 96.6) -> dict[str, Any]:
    """a 0 to ``end`` (its end a transition, so a phrase end in reach is selected), b, c whole."""
    pins = {} if end_pin is None else {"end_pin": end_pin}
    return mix(piece("a", 0.0, end, **pins), piece("b", 0.0, 240.0), piece("c", 0.0, 240.0))


def end_of_a(value: dict[str, Any], known: dict[str, TrackFacts] | None = None) -> float:
    return checked(value, ORDER, known or facts())["tracks"][0]["end_sec"]


def refused(value: dict[str, Any], known: dict[str, TrackFacts] | None = None) -> str:
    with pytest.raises(MixPlanRefused) as caught:
        checked(value, ORDER, known or facts())
    return caught.value.reason


# --- where a pinned cut lands ---------------------------------------------------------------------


def test_an_ai_candidate_never_replaces_a_pin() -> None:
    """Unpinned, the phrase end at 97.4 (0.8 s away) is selected and snapped to 98; a pin skips
    the selection whatever its mode."""
    assert end_of_a(first_a()) == 98.0
    assert end_of_a(first_a(pin(96.6, "bar"))) == 96.0  # snapped(96.6), not 97.4 -> 98
    assert end_of_a(first_a(pin(96.6, "exact"))) == 96.6
    assert end_of_a(first_a(pin(96.6, "bar", confirmed=95.37))) == 95.37  # never re-snapped
    assert end_of_a(first_a(pin(96.6, "exact", confirmed=97.01))) == 97.01


def test_a_pin_sets_the_cut_whatever_the_stored_field_says() -> None:
    assert end_of_a(first_a(pin(96.6, "exact"), end=200.0)) == 96.6


def test_a_bar_pin_on_a_track_without_a_trusted_grid_keeps_its_request_rounded() -> None:
    known = facts(replace(A0, timing_class="approximate"))
    assert end_of_a(first_a(pin(96.6, "bar")), known) == 96.6


def test_the_checked_pinned_spec_is_stored_and_a_fixed_point() -> None:
    value = mix(
        piece("a", 0.0, 96.6, start_pin=pin(0.0, "exact"), end_pin=pin(96.6, "bar")),
        piece("b", 0.0, 240.0, end_pin=pin(200.3, "exact", confirmed=200.3)),
        piece("c", 0.0, 240.0),
    )
    good = checked(value, ORDER, facts())
    assert good["tracks"][0] == {
        "reference": "a",
        "start_sec": 0.0,
        "end_sec": 96.0,
        "start_pin": {"requested_sec": 0.0, "mode": "exact", "confirmed": False},
        "end_pin": {"requested_sec": 96.6, "mode": "bar", "confirmed": False},
    }
    assert good["tracks"][1]["end_pin"] == pin(200.3, "exact", confirmed=200.3)
    assert good["tracks"][2] == piece("c", 0.0, 240.0)  # no pin key at all
    assert checked(good, ORDER, facts()) == good
    assert MixSpec.model_validate(good).model_dump(mode="json") == good


def test_a_spec_without_a_pin_is_byte_identical_and_hashes_as_before() -> None:
    legacy = {
        "tracks": [
            {"reference": "a", "start_sec": 0.0, "end_sec": 98.0},
            {"reference": "b", "start_sec": 0.0, "end_sec": 240.0},
            {"reference": "c", "start_sec": 0.0, "end_sec": 240.0},
        ],
        "crossfades": [0, 0],
    }
    good = checked(first_a(), ORDER, facts())
    stored = MixSpec.model_validate(good)
    assert good == legacy
    assert stored.model_dump(mode="json") == legacy
    assert stored.model_dump() == legacy
    assert json.loads(stored.model_dump_json()) == legacy
    content = {"name": "x"}
    assert studio_hash(content, stored.model_dump(mode="json")) == studio_hash(content, legacy)


# --- every hard rule still refuses with a pin present ---------------------------------------------

EXACT = pin(96.0, "exact")


def loop(
    first: tuple[float, float],
    second: tuple[float, float],
    *,
    end_pin: dict[str, Any] | None = None,
    start_pin: dict[str, Any] | None = None,
    crossfades: list[Any] | None = None,
) -> dict[str, Any]:
    """a loops (two pieces back to back), then b and c whole."""
    one = piece("a", *first, **({"end_pin": end_pin} if end_pin else {}))
    two = piece("a", *second, **({"start_pin": start_pin} if start_pin else {}))
    return mix(one, two, piece("b", 0.0, 240.0), piece("c", 0.0, 240.0), crossfades=crossfades)


LONG = TrackFacts(3600.0, 120.0, 0.0, "exact")


@pytest.mark.parametrize(
    ("value", "known", "reason"),
    [
        (first_a(pin(240.5, "exact")), None, "cut_out_of_range"),  # past the length
        (first_a(pin(240.5, "bar", confirmed=240.5)), None, "cut_out_of_range"),
        (mix(piece("a", 0.0, 96.0, start_pin=pin(120.0, "exact"), end_pin=EXACT),
             piece("b", 0.0, 240.0), piece("c", 0.0, 240.0)), None, "cut_out_of_range"),
        (first_a(pin(50.0, "exact")), None, "window_too_short"),
        (first_a(pin(59.99, "bar", confirmed=59.99)), None, "window_too_short"),
        (mix(piece("a", 0.0, 96.0, end_pin=EXACT), piece("b", 0.0, 240.0)), None,
         "not_a_permutation"),  # c has no piece
        (mix(piece("a", 0.0, 96.0), piece("b", 0.0, 240.0), piece("c", 0.0, 240.0),
             piece("z", 0.0, 100.0, end_pin=EXACT)), facts(z=PLAIN), "not_a_permutation"),
        (mix(piece("a", 0.0, 96.0, end_pin=EXACT), piece("b", 0.0, 240.0),
             piece("c", 0.0, 240.0), crossfades=[0]), None, "crossfade_count"),
        (mix(piece("a", 0.0, 96.0, end_pin=EXACT), piece("b", 0.0, 240.0),
             piece("c", 0.0, 240.0), crossfades=[1.5, 0]), None, "crossfade_not_whole"),
        (mix(piece("a", 0.0, 96.0, end_pin=EXACT), piece("b", 0.0, 240.0),
             piece("c", 0.0, 240.0), crossfades=[31, 0]), None, "crossfade_out_of_range"),
        (loop((0.0, 64.0), (32.0, 52.0), end_pin=pin(64.0, "exact"), crossfades=[1, 0, 0]),
         None, "loop_crossfade_not_zero"),
        # the loop run's second piece plays 20 s: a 25 s crossfade out of it is too long
        (loop((0.0, 64.0), (32.0, 52.0), start_pin=pin(32.0, "exact"), crossfades=[0, 25, 0]),
         None, "crossfade_too_long"),
        # b pinned to 60 s in the middle: 30 + 30 s of crossfade leave none of it alone
        (mix(piece("a", 0.0, 240.0), piece("b", 0.0, 240.0, end_pin=pin(60.0, "exact")),
             piece("c", 0.0, 240.0), crossfades=[30, 30]), None, "crossfades_overlap"),
        (mix(piece("a", 0.0, 3600.0, start_pin=pin(0.0, "exact")), piece("b", 0.0, 3600.0),
             piece("c", 0.0, 3600.0)), {"a": LONG, "b": LONG, "c": LONG}, "mix_too_long"),
        # the loop rules, owner default: no exact-pin override
        (loop((0.0, 64.0), (32.0, 160.0), end_pin=pin(64.0, "exact")),
         facts(replace(A0, timing_class="approximate")), "loop_needs_exact_timing"),
        (loop((0.0, 64.0), (32.0, 160.0), end_pin=pin(63.0, "exact")), None,
         "loop_not_on_phrase"),
        (loop((0.0, 64.0), (32.0, 160.0), start_pin=pin(33.0, "exact")), None,
         "loop_not_on_phrase"),
        (loop((0.0, 64.0), (32.0, 160.0), end_pin=pin(64.0, "bar", confirmed=63.9)), None,
         "loop_not_on_phrase"),
        (loop((0.0, 64.0), (52.0, 60.0), start_pin=pin(52.0, "exact")), None,
         "loop_piece_too_short"),
        # the shape of a piece and of a pin
        (first_a({**EXACT, "tempo": 1.0}), None, "unknown_field"),
        (mix(piece("a", 0.0, 96.0, end_pin=EXACT, tempo=1.0), piece("b", 0.0, 240.0),
             piece("c", 0.0, 240.0)), None, "tempo_change"),
        (mix(piece("a", 0.0, 96.0, end_pin=EXACT, pin=EXACT), piece("b", 0.0, 240.0),
             piece("c", 0.0, 240.0)), None, "unknown_field"),
        (first_a({"requested_sec": 96.0, "mode": "loose", "confirmed": False}), None,
         "unknown_field"),
        (first_a({"requested_sec": 96.0, "mode": "bar", "confirmed": True}), None,
         "unknown_field"),  # confirmed without its cut and file
        (first_a({**pin(96.0, "bar", confirmed=96.0), "confirmed_against": "x"}), None,
         "unknown_field"),
        (first_a({"requested_sec": 96.005, "mode": "exact", "confirmed": False}), None,
         "unknown_field"),  # not rounded to 0.01 s
        (first_a(None) | {"tracks": [piece("a", 0.0, 96.0, end_pin=None),
                                     piece("b", 0.0, 240.0), piece("c", 0.0, 240.0)]}, None,
         "unknown_field"),  # an absent pin is no key, never null
    ],
)  # fmt: skip
def test_every_hard_rule_still_refuses_with_a_pin(
    value: dict[str, Any], known: dict[str, TrackFacts] | None, reason: str
) -> None:
    assert refused(value, known) == reason


def test_the_same_loops_pass_on_their_phrase_points() -> None:
    """The cases above refuse for the pin, not the spec: on the anchors the loop holds."""
    assert checked(loop((0.0, 64.0), (32.0, 160.0), end_pin=pin(64.0, "exact")), ORDER, facts())
    good = checked(loop((0.0, 64.0), (32.0, 52.0), start_pin=pin(32.0, "exact")), ORDER, facts())
    assert good["tracks"][1]["start_pin"] == pin(32.0, "exact")


# --- mode x confirmed x reanalysis x music_changed -----------------------------------------------

# (mode, confirmed_sec): the effective cut on the old grid and after a reanalysis (+0.12 s)
PINS = {
    ("bar", None): (96.0, 96.12),
    ("bar", 96.0): (96.0, 96.0),
    ("exact", None): (96.6, 96.6),
    ("exact", 96.6): (96.6, 96.6),
}


@pytest.mark.parametrize("music_changed", [False, True])
@pytest.mark.parametrize("reanalysed", [False, True])
@pytest.mark.parametrize(("mode", "confirmed"), list(PINS))
def test_the_pin_table(
    mode: str, confirmed: float | None, reanalysed: bool, music_changed: bool
) -> None:
    value = first_a(pin(96.6, mode, confirmed))
    before = checked(value, ORDER, facts(A0))
    after = checked(before, ORDER, facts(A1 if reanalysed else A0))
    cut = after["tracks"][0]["end_sec"]
    assert cut == PINS[mode, confirmed][reanalysed]
    if confirmed is not None:
        assert cut == confirmed  # a confirmed pin never moves, across reanalysis too
    analysed = {"a": NEW if music_changed else OLD, "b": OLD, "c": OLD}
    expected = [(0, "end")] if confirmed is not None and music_changed else []
    assert listen_again(after, analysed) == expected
    hints = mix_hint_policy.pin_hints(after, facts(A1 if reanalysed else A0), before)
    codes = {h.code for h in hints}
    moved = mode == "bar" and confirmed is None and reanalysed
    assert ("pin_moved" in codes) == moved
    off_grid = (cut * 100 - (12 if reanalysed else 0)) % 200 != 0
    assert ("off_beat_cut" in codes) == ((mode == "exact" or confirmed is not None) and off_grid)
    if moved:
        (hint,) = [h for h in hints if h.code == "pin_moved"]
        assert hint.as_json() == {
            "code": "pin_moved",
            "pieces": [0],
            "edge": "end",
            "moved_sec": 0.12,
        }
        assert "+0.12 s" in hint.text()


def test_listen_again_fails_closed_without_a_record_of_the_file() -> None:
    good = checked(first_a(pin(96.6, "exact", confirmed=96.6)), ORDER, facts())
    assert listen_again(good, {}) == [(0, "end")]
    assert listen_again(good, {"a": None}) == [(0, "end")]
    assert listen_again(checked(first_a(pin(96.6, "exact")), ORDER, facts()), {}) == []


def test_the_pins_hints_name_pieces_never_references() -> None:
    good = checked(first_a(pin(96.6, "exact")), ORDER, facts())
    (hint,) = mix_hint_policy.pin_hints(good, facts())
    assert hint.as_json() == {"code": "off_beat_cut", "pieces": [0], "edge": "end"}
    assert '"a"' not in json.dumps(hint.as_json())
    assert mix_hint_policy.pin_hints(checked(first_a(), ORDER, facts()), facts()) == []


@pytest.mark.parametrize(
    "bad",
    [
        {**pin(96.0, "exact"), "extra": 1},
        {"requested_sec": 96.0, "mode": "exact", "confirmed": False, "confirmed_sec": 96.0},
        {"requested_sec": 96.0, "mode": "exact", "confirmed": True, "confirmed_sec": 96.0},
    ],
)
def test_the_stored_shape_refuses_a_pin_that_does_not_read(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MixSpec.model_validate(first_a(bad, end=96.0))


def test_the_stored_shape_refuses_an_exact_or_confirmed_pin_that_is_not_its_cut() -> None:
    with pytest.raises(ValueError, match="is its cut"):
        MixSpec.model_validate(first_a(pin(96.6, "exact"), end=96.0))
    with pytest.raises(ValueError, match="is its cut"):
        MixSpec.model_validate(first_a(pin(96.6, "bar", confirmed=95.0), end=96.0))
    MixSpec.model_validate(first_a(pin(96.6, "bar"), end=96.0))  # a bar pin's cut is the snap


def test_a_pin_never_reaches_the_gateway_body_or_the_render_plan() -> None:
    """What ``produce_outputs`` writes is the effective cut only: the same body and plan as the
    spec with its pins stripped."""
    pinned = checked(
        mix(
            piece("a", 0.0, 96.6, start_pin=pin(2.0, "exact"), end_pin=pin(96.6, "exact")),
            piece("b", 0.0, 240.0, end_pin=pin(200.3, "bar", confirmed=200.3)),
            piece("c", 0.0, 240.0),
            crossfades=[8, 6],
        ),
        ORDER,
        facts(),
    )
    bare = {
        **pinned,
        "tracks": [
            {k: v for k, v in t.items() if not k.endswith("_pin")} for t in pinned["tracks"]
        ],
    }
    known = {r: Facts("Song", "Band", 120.0) for r in ORDER}
    durations = dict.fromkeys(ORDER, 240.0)
    body = playlist_body("Class", pinned, known, durations)
    assert body == playlist_body("Class", bare, known, durations)
    assert "_pin" not in json.dumps(body)
    assert render_plan(pinned, durations) == render_plan(bare, durations)
