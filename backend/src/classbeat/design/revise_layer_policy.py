"""A revise request's stated values layered onto the class's (ADR 0029 section 4, #904; pure).

``layer(base, read, gates)``: ``base`` is the brief the revise starts from (the class's, with the
nearest stated layer of its revisions applied: ``chat.load_base``); ``read`` is the interpreter's
reading of the revise request, made with the base's values as ``current`` (``revise_stated``), so
it names what the class should have after the change, for what the request changes only.

- ``sport`` and ``music`` are never changed by a revise (the sport is the class's; the music has
  Change songs) and are ignored;
- a knob the request does not mention is unchanged, stated or not: an untouched stated value
  stays a constraint (the old explicit list is kept);
- a knob the request states, within the hard limits (``interpret_policy.held``): the brief takes
  it and it is stated, replacing the earlier stated value of that path;
- a knob it states beyond a limit: the brief takes the nearest allowed value (or keeps its own
  where the limit leaves the value out), the path is no longer stated, and a Not as asked item
  records the asked value and the reason code, as at interpret time;
- a knob it only implies ("a bit shorter", ``inferred``): the brief takes it (held quietly) and
  the path is no longer stated: the instructor changed it without a value to keep;
- games: unchanged when the request reports none and does not remove them; ``remove_games``
  clears them; otherwise the reported list is the whole list after the change. A reported game is
  stated if the request states it, or if it equals a game the base stated (an untouched stated
  game stays one). A game is held as at interpret time (dropped on a running class or a plan
  without games, held to MOWL's 10 s floor, never swapped), with Not as asked for a stated one.

Every value is closed (numbers, short enums, a game as a small object). No quote is kept: a quote
must be a substring of ``brief.text``, the first request, and these are the revise request's.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from classbeat.agent.brief import Brief, BriefGame
from classbeat.agent.explicit_check import MIN_GAME_SEC
from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.design import request_policy
from classbeat.design.interpret_policy import KNOBS, Offer, held
from classbeat.plan_gates import PlanGates

_ORDER: Final = ("sport", *KNOBS, *(f"games.{n}" for n in range(6)))


@dataclass(frozen=True)
class Layered:
    """The layered brief and the revise request's Not as asked items (none: as the brief says)."""

    brief: Brief
    items: tuple[InterpretationItem, ...] = field(default_factory=tuple)


def _not_as_asked(path: str, value: Any, reason: str) -> InterpretationItem:
    return InterpretationItem(path=path, origin="not_as_asked", reason_code=reason, value=value)


def _games(
    base: Brief, read: InterpretedRequest, gates: PlanGates | None, out: list[InterpretationItem]
) -> tuple[list[BriefGame], list[str]]:
    stated = sorted(int(p[6:]) for p in base.explicit if p.startswith("games."))
    if not read.games:
        if read.remove_games:
            return [], []
        return list(base.games), [f"games.{n}" for n in stated]
    limits = request_policy.Limits.from_gates(gates, (base.sport,))
    was_stated = [base.games[n].model_dump(mode="json", exclude_none=True) for n in stated]
    games: list[BriefGame] = []
    paths: list[str] = []
    for n, wanted in enumerate(read.games):
        game = wanted.brief_game()
        value = game.model_dump(mode="json", exclude_none=True)
        kept = value in was_stated
        if kept:
            was_stated.remove(value)  # each stated game of the base keeps one reported game
        asked = wanted.origin == "explicit" or kept
        reason = request_policy.hard_limit(f"games.{n}", value, limits, base.sport)
        if reason in ("game_on_running", "plan_no_games"):
            if asked:
                out.append(_not_as_asked(f"games.{n}", value, reason))
            continue
        if reason == "game_under_10s":  # the nearest allowed length, never another game
            game = BriefGame(type=game.type, placement=game.placement, duration_sec=MIN_GAME_SEC)
            if asked:
                out.append(_not_as_asked(f"games.{n}", value, reason))
        if asked:
            paths.append(f"games.{len(games)}")
        games.append(game)
    return games, paths


def _ordered(paths: Sequence[str]) -> list[str]:
    wanted = set(paths)
    return [p for p in _ORDER if p in wanted]


def layer(base: Brief, read: InterpretedRequest, gates: PlanGates | None) -> Layered:
    """The module's rules: the brief the revise keeps and the items it records."""
    offer = Offer(sports=(base.sport,), gates=gates)
    fields: dict[str, Any] = base.model_dump(mode="python")
    explicit = set(base.explicit)
    items: list[InterpretationItem] = []
    readings = {r.path: r for r in read.readings() if r.path in KNOBS}
    for path in KNOBS:
        reading = readings.get(path)
        if reading is None:
            continue
        explicit.discard(path)
        value, reason = held(path, reading.value, base.sport, offer)
        if reason is not None and reading.origin == "explicit":
            items.append(_not_as_asked(path, reading.value, reason))
        if value is not None:
            fields[path] = value
            if reading.origin == "explicit" and reason is None:
                explicit.add(path)
    explicit = {p for p in explicit if not p.startswith("games.")}
    if base.sport == "cycling":
        games, game_paths = _games(base, read, gates, items)
        fields["games"] = games
        explicit.update(game_paths)
    else:
        _games(base, read, gates, items)  # every game of a running class is Not as asked
    fields["explicit"] = _ordered(sorted(explicit))
    return Layered(Brief.model_validate(fields), tuple(items))
