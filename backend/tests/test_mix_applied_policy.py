"""ADR 0036 amendment B9 (#1370), the pure parts: the reason order of
``output_choice_policy.refusal`` with ``mixed`` and ``applied_form``, and
``studio.mix_applied_policy`` (``mixed``, B4's proposal allowlist, the basis of an applied draft,
``holds``). No database, no network."""

import itertools
import json
import uuid
from typing import Any

import pytest

from classbeat.studio import mix_applied_policy
from classbeat.studio.mix_applied_policy import (
    APPLIED_ORIGIN,
    MAX_CHAIN,
    PROPOSAL_ORIGINS,
    STUDIO_SOURCE,
    Link,
    basis,
    holds,
    implied,
    mixed,
    proposal_of,
)
from classbeat.studio.mix_choice_model import MixChoices, canonical
from classbeat.studio.mix_choice_policy import applied
from classbeat.studio.output_choice_policy import OUTPUTS, refusal
from classbeat.swedebeat_studio.song_list_model import SOURCE
from mix_choice_fixtures import FACTS, ORDER, PLAIN, REPEAT, proposal, spec, track

ALL = frozenset(OUTPUTS)
ORDERED = (
    "invalid_input",
    "mowl_needs_mix",
    "mix_not_chosen",
    "mix_not_applied",
    "playlist_has_repeats",
    "output_off",
)
WHOLE = MixChoices.whole_songs()
DJ = MixChoices.dj_defaults()
FULL = spec(*((r, 0.0, 240.0) for r in ORDER), crossfades=[0, 0, 0])


def case(triggers: tuple[str, ...]) -> tuple[list[str], frozenset[str], dict[str, bool]]:
    """The SwedeBeat playlist alone, refused by nothing, with each of ``triggers`` added."""
    chosen = ["swedebeat_playlist"]
    enabled = ALL
    facts = {"repeats": False, "mixed": False, "applied_form": True}
    if "mowl_needs_mix" in triggers:
        chosen.append("mowl")
    if "mix_not_chosen" in triggers:
        facts["mixed"] = True
    if "mix_not_applied" in triggers:
        facts["applied_form"] = False
    if "playlist_has_repeats" in triggers:
        facts["repeats"] = True
    if "output_off" in triggers:
        enabled = ALL - {"swedebeat_playlist"}
    if "invalid_input" in triggers:
        chosen.append("swedebeat_playlist")
    return chosen, enabled, facts


SUBSETS = [c for n in range(len(ORDERED) + 1) for c in itertools.combinations(ORDERED, n)]


@pytest.mark.parametrize("triggers", SUBSETS, ids=lambda t: "+".join(t) or "none")
def test_the_first_reason_of_the_fixed_order_wins_for_every_combination(
    triggers: tuple[str, ...],
) -> None:
    """Every pair (and every larger set) of reasons: the earliest in ``ORDERED`` is the answer."""
    chosen, enabled, facts = case(triggers)
    expected = next((r for r in ORDERED if r in triggers), None)
    assert refusal(chosen, enabled, **facts) == expected


@pytest.mark.parametrize(
    ("chosen", "is_mixed", "is_applied", "reason"),
    [
        (["swedebeat_playlist"], False, True, None),  # a plain draft for the playlist alone
        (["spotify_playlist"], True, False, None),  # Spotify alone: from any draft (A8)
        (["swedebeat_playlist", "swedebeat_mix"], True, True, None),  # mixed, with DJ mix
        (["swedebeat_mix"], False, True, None),  # a rendered whole-songs mix (MOWL needs one)
        (["swedebeat_mix", "mowl"], True, True, None),
        (["swedebeat_playlist"], True, True, "mix_not_chosen"),
        (["spotify_playlist", "swedebeat_playlist"], True, True, "mix_not_chosen"),
        (["swedebeat_mix"], True, False, "mix_not_applied"),
        (["swedebeat_playlist"], False, False, "mix_not_applied"),
        (["swedebeat_mix", "mowl"], False, False, "mix_not_applied"),
        (["spotify_playlist", "swedebeat_mix"], False, False, "mix_not_applied"),
    ],
)
def test_the_two_facts(
    chosen: list[str], is_mixed: bool, is_applied: bool, reason: str | None
) -> None:
    assert refusal(chosen, ALL, repeats=False, mixed=is_mixed, applied_form=is_applied) == reason


# --- mixed ----------------------------------------------------------------------------------


def test_mixed_is_not_whole_songs_and_fails_closed() -> None:
    assert not mixed(FULL, FACTS)
    assert mixed(PLAIN, FACTS)  # cuts and crossfades
    assert mixed(REPEAT, FACTS)
    assert mixed(spec(*((r, 0.0, 240.0) for r in ORDER), crossfades=[0, 3, 0]), FACTS)
    assert mixed(FULL, {r: FACTS[r] for r in ORDER[:3]})  # a song without its analysis
    assert mixed(FULL, {r: track(length=239.5) for r in ORDER})  # not its analysed length
    assert mixed({"tracks": [{"reference": "a"}], "crossfades": []}, FACTS)  # does not read


# --- the proposal and the basis -------------------------------------------------------------

LIST = str(uuid.uuid4())
OTHER_LIST = str(uuid.uuid4())


def link(
    origin: str | None,
    song_list: str | None = LIST,
    kind: str = "draft",
    source: str | None = STUDIO_SOURCE,
) -> Link:
    return Link(uuid.uuid4(), kind, song_list, origin, source)


def test_the_studio_source_is_the_song_lists() -> None:
    assert STUDIO_SOURCE == SOURCE


def test_the_proposal_is_the_nearest_allowlisted_studio_draft_on_the_same_list() -> None:
    assert frozenset({"design_draft", "revise_draft"}) == PROPOSAL_ORIGINS
    first = link("revise_draft")
    older = link("design_draft")
    passed_over = [  # studio drafts of the same list whose origin is not a model design
        link(APPLIED_ORIGIN),
        link("duplicate"),
        link("beat_one"),
        link(None),
        link("studio_songs"),
        link("refit_draft"),  # any origin added later until the allowlist names it
    ]
    assert proposal_of([*passed_over, first, older], LIST) == first.revision_id
    assert proposal_of(passed_over, LIST) is None
    assert proposal_of([], LIST) is None


@pytest.mark.parametrize(
    "barrier",
    [
        link("design_draft", OTHER_LIST),  # another list
        link("design_draft", kind="song_list"),  # not a draft
        link("design_draft", source="spotify"),  # a draft of another music source
        link("design_draft", source=None),
        link(APPLIED_ORIGIN, None),  # no list
    ],
)
def test_the_walk_stops_at_a_revision_that_is_no_studio_draft_of_the_list(barrier: Link) -> None:
    """#1402's rule (``mix_proposal_policy.step``): stop, never pass over to an older proposal."""
    older = link("design_draft")
    assert proposal_of([link(APPLIED_ORIGIN), barrier, older], LIST) is None
    assert proposal_of([barrier, older], LIST) is None


def test_the_walk_reads_at_most_max_chain_revisions() -> None:
    proposed = link("design_draft")
    assert MAX_CHAIN == 500
    within = [*(link(APPLIED_ORIGIN) for _ in range(MAX_CHAIN - 1)), proposed]
    assert proposal_of(within, LIST) == proposed.revision_id
    assert proposal_of([link(APPLIED_ORIGIN), *within], LIST) is None


def test_a_proposal_is_its_own_basis_with_the_choices_its_outputs_imply() -> None:
    own = link("design_draft")
    found = basis([own], LIST, {}, ["swedebeat_playlist"])
    assert found is not None
    assert (found.proposal_id, found.choices) == (own.revision_id, WHOLE)
    found = basis([own], LIST, {}, ["swedebeat_playlist", "swedebeat_mix"])
    assert found is not None
    assert found.choices == DJ
    assert implied(["mowl", "swedebeat_mix"]) == DJ
    assert implied(["spotify_playlist"]) == WHOLE


def test_an_applied_draft_has_its_recorded_proposal_and_choices() -> None:
    own, proposed = link(APPLIED_ORIGIN), link("revise_draft")
    choices = MixChoices(dj_mix=True, blend=False, trim=True, keep_whole=(1,))
    record: dict[str, Any] = {
        "proposal_revision_id": str(proposed.revision_id),
        "choices": json.loads(canonical(choices)),
    }
    found = basis([own, link(APPLIED_ORIGIN), proposed], LIST, record, ["swedebeat_mix"])
    assert found is not None
    assert (found.proposal_id, found.choices) == (proposed.revision_id, choices)


@pytest.mark.parametrize(
    "record",
    [
        {},  # no id
        {"proposal_revision_id": "not-a-uuid", "choices": {"dj_mix": False}},
        {"proposal_revision_id": 7, "choices": {}},
        {"choices": {"dj_mix": False, "blend": False, "trim": False}},
        {"proposal_revision_id": "<proposal>", "choices": {"dj_mix": False, "blend": True}},
        {"proposal_revision_id": "<proposal>", "choices": None},
        {"proposal_revision_id": "<older>", "choices": {"dj_mix": False, "blend": False,
                                                       "trim": False}},
    ],
)  # fmt: skip
def test_an_applied_draft_with_a_record_that_does_not_hold_has_no_basis(
    record: dict[str, Any],
) -> None:
    own, proposed, older = link(APPLIED_ORIGIN), link("revise_draft"), link("design_draft")
    ids = {"<proposal>": str(proposed.revision_id), "<older>": str(older.revision_id)}
    given = {k: ids.get(v, v) if isinstance(v, str) else v for k, v in record.items()}
    assert basis([own, proposed, older], LIST, given, ["swedebeat_mix"]) is None


def test_any_other_draft_has_no_basis() -> None:
    proposed = link("design_draft")
    for origin in ("duplicate", "beat_one", None, "sketch", "refit_draft"):
        assert basis([link(origin), proposed], LIST, {}, ["swedebeat_playlist"]) is None
    assert basis([], LIST, {}, ["swedebeat_playlist"]) is None
    # A proposal on another list than the draft's, or of another source, has none either.
    assert basis([link("design_draft", OTHER_LIST)], LIST, {}, ["swedebeat_playlist"]) is None
    assert basis([link("design_draft", source="x")], LIST, {}, ["swedebeat_playlist"]) is None


# --- holds ------------------------------------------------------------------------------------


def test_a_proposal_holds_only_as_the_applied_form_of_its_choices() -> None:
    assert holds(PLAIN, proposal(PLAIN), FACTS, DJ)  # the proposal's own form with DJ mix
    assert not holds(PLAIN, proposal(PLAIN), FACTS, WHOLE)  # not whole songs
    assert holds(applied(proposal(PLAIN), FACTS, WHOLE), proposal(PLAIN), FACTS, WHOLE)


def test_a_cut_on_a_track_that_is_not_exact_never_holds() -> None:
    for timing in ("approximate", "unusable", None):
        facts = {**FACTS, "b": track(timing)}
        assert not holds(PLAIN, proposal(PLAIN), facts, DJ)  # b is cut 20 to 220


def test_outside_the_class_length_never_holds() -> None:
    whole = applied(proposal(FULL), FACTS, WHOLE)  # 960 s
    assert holds(whole, proposal(FULL), FACTS, WHOLE)
    assert not holds(whole, proposal(FULL, target=1200), FACTS, WHOLE)  # under 90 %
    assert not holds(whole, proposal(FULL, target=500), FACTS, WHOLE)  # over 150 %


def test_a_refusal_or_anything_unreadable_does_not_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    gap = proposal(PLAIN)
    gap = type(gap)(("a", None, "c", "d"), gap.spec, gap.target_sec)
    assert not holds(PLAIN, gap, FACTS, DJ)  # list_has_gap from applied
    assert not holds(PLAIN, proposal(PLAIN), {r: FACTS[r] for r in ORDER[:3]}, DJ)
    off_list = MixChoices(dj_mix=True, blend=True, trim=True, keep_whole=(9,))
    assert not holds(PLAIN, proposal(PLAIN), FACTS, off_list)  # invalid_input
    for error in (KeyError("x"), TypeError("x"), IndexError("x"), ValueError("x")):

        def raising(*_args: object, error: Exception = error) -> Any:
            raise error

        monkeypatch.setattr(mix_applied_policy, "applied", raising)
        assert not holds(PLAIN, proposal(PLAIN), FACTS, DJ)
    monkeypatch.setattr(mix_applied_policy, "applied", lambda *_: {"tracks": [], "crossfades": []})
    assert not holds(PLAIN, proposal(PLAIN), FACTS, DJ)  # not a stored spec (ValidationError)


def test_mixed_fails_closed_when_its_check_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for error in (KeyError("x"), TypeError("x"), ValueError("x")):

        def raising(*_args: object, error: Exception = error) -> bool:
            raise error

        monkeypatch.setattr(mix_applied_policy, "is_plain", raising)
        assert mixed(FULL, FACTS)
