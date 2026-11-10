"""The suite's game checks (ADR 0029 sections 2 and 3; pure: no database, network or model).

- ``games_result``: the games of the fitted spec against a case's ``expect``: each listed game
  exactly once and no other, or (``game_any``, a request that names no type) exactly one game of
  a type the catalogue offers. ``workout.validate`` already holds the library's rules for them.
- ``edit_result``: a game edit of the article (G11) made by a revise case, measured against the
  base: the targeted interval changed as asked and **every other interval is as it was**. Intervals
  are compared by zone, position (standing) and game, never by length: fitting to the music moves
  a boundary by a few seconds, so a length counts only where the edit is about it, within
  ``SEC_TOLERANCE``.

A detail holds numbers and rule names only, never draft text.
"""

from typing import Any, Final

from classbeat.agent.games_catalog import TYPES
from classbeat.evals.cases import Expect, RevisionExpect

EDITS: Final = frozenset(
    {"game_added", "game_replaced", "game_type", "game_removed", "game_length", "game_zone"}
)
SEC_TOLERANCE: Final = 10  # seconds fitting may move an edited interval's end


def _flat(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for s in spec["segments"] for i in s["intervals"]]


def _keys(intervals: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [(i["zone"], bool(i.get("standing")), i.get("game")) for i in intervals]


def games_result(expect: Expect, spec: dict[str, Any]) -> tuple[bool, str] | None:
    """(ok, detail) for the case's games, ``None`` when it asks for none."""
    found = sorted(str(i["game"]) for i in _flat(spec) if i.get("game"))
    if expect.games:
        return found == sorted(expect.games), f"games {found} for {sorted(expect.games)}"
    if expect.game_any:
        return len(found) == 1 and found[0] in TYPES, f"games {found}, one of {list(TYPES)}"
    return None


def _without(items: list[Any], at: int) -> list[Any]:
    return items[:at] + items[at + 1 :]


def edit_result(
    want: RevisionExpect, spec: dict[str, Any], base: dict[str, Any]
) -> tuple[bool, str]:
    """(ok, detail) for a game edit; ``want.kind`` is one of ``EDITS``."""
    new, old, at = _flat(spec), _flat(base), want.at or 0
    if at >= len(old) + (want.kind == "game_added"):
        return False, f"interval {at} is outside the base ({len(old)} intervals)"
    kind = want.kind
    if kind == "game_added":
        got = new[at].get("game") if at < len(new) else None
        ok = len(new) == len(old) + 1 and _keys(_without(new, at)) == _keys(old)
        return ok and got == want.game, f"{len(new)} intervals from {len(old)}, game at {at}: {got}"
    target, was = new[min(at, len(new) - 1)], old[at]
    if kind == "game_removed":
        deleted = len(new) == len(old) - 1 and _keys(new) == _keys(_without(old, at))
        turned = len(new) == len(old) and target.get("game") is None
        turned = turned and target["zone"] == was["zone"]
        turned = turned and _keys(_without(new, at)) == _keys(_without(old, at))
        return deleted or turned, f"{len(new)} intervals from {len(old)}, deleted {deleted}"
    same_rest = len(new) == len(old) and _keys(_without(new, at)) == _keys(_without(old, at))
    if not same_rest:
        return False, f"{len(new)} intervals from {len(old)}, another interval changed"
    wanted: dict[str, Any] = {
        "game_replaced": {"game": want.game, "zone": was["zone"]},
        "game_type": {"game": want.game, "zone": was["zone"]},
        "game_length": {"game": was.get("game"), "zone": was["zone"]},
        "game_zone": {"game": was.get("game"), "zone": want.zone},
    }[kind]
    asked = want.sec if kind == "game_length" else was["sec"]
    ok = target.get("game") == wanted["game"] and target["zone"] == wanted["zone"]
    near = abs(target["sec"] - (asked or 0)) <= SEC_TOLERANCE
    return ok and near, f"interval {at}: zone {target['zone']}, {target['sec']} s for {asked} s"
