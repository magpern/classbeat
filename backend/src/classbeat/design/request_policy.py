"""What the instructor asked, what the system assumed, and what could not be done (ADR 0029
section 4; pure: no database, no network, no model).

The rules, in one place:

- an **explicit** value (one of ``Brief.explicit``) is never replaced: not by an inferred value, not
  by a default, not by a soft rule. A later explicit value of the same path (a revise request)
  replaces an earlier one (``merge``);
- a **hard limit** (the plan's class length, a plan without games, a game under MOWL's 10 s floor,
  a sport that is not offered) is the one thing that overrides it, and the item is then recorded
  as Not as asked with a reason code, never silently;
- every other value of the brief is an assumption (``inferred`` or ``default``) and is recorded as
  Assumed; a stated value the finished draft misses (``agent.explicit_check``) is Not as asked;
- music is taken in a fixed order per sport (``music_order``).

``interpretation_for`` turns that into the ``provenance.interpretation`` items. It reads the
brief's closed values only; a quote is kept only if it is a substring of ``brief.text``.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from classbeat.agent.brief import Brief
from classbeat.agent.explicit_check import MIN_GAME_SEC, Miss
from classbeat.agent.interpretation_record import InterpretationItem, Origin, keep_quote
from classbeat.plan_gates import PlanGates

ALL_SPORTS: Final = ("cycling", "running")
# The music of a class, first available wins (cycling may end without music; running may not).
CYCLING_MUSIC: Final = ("override", "name_match", "pick", "first_playlist", "soundless")
RUNNING_MUSIC: Final = ("override", "name_match", "first_playlist")
NEEDS_MOWL_PLAYLIST: Final = "needs_mowl_playlist"
_ASSUMED_FIELDS: Final = (
    "length_min",
    "intensity",
    "climbs",
    "sprints",
    "races",
    "ftp_test",
    "class_type",
)


@dataclass(frozen=True)
class Requested:
    """One value and where it came from: ``origin`` is ``explicit``, ``inferred`` or ``default``."""

    path: str
    value: Any
    origin: Origin = "explicit"
    quote: str | None = None


@dataclass(frozen=True)
class Limits:
    max_class_minutes: int | None = None
    games: bool = True
    sports: tuple[str, ...] = ALL_SPORTS

    @classmethod
    def from_gates(cls, gates: PlanGates | None, sports: Sequence[str] = ALL_SPORTS) -> "Limits":
        if gates is None:
            return cls(sports=tuple(sports))
        return cls(gates.max_class_minutes, gates.games, tuple(sports))


def allowed_sports(*, running_enabled: bool, plan_runs: bool) -> tuple[str, ...]:
    """Cycling always; running only when the switch is on and the plan includes it."""
    return ALL_SPORTS if running_enabled and plan_runs else ALL_SPORTS[:1]


def music_order(sport: str) -> tuple[str, ...]:
    return RUNNING_MUSIC if sport == "running" else CYCLING_MUSIC


def choose_music(sport: str, available: Collection[str]) -> str | None:
    """The first source of the sport's order that is available; ``None`` only for running with no
    MOWL playlist (Not as asked: ``needs_mowl_playlist``)."""
    return next((s for s in music_order(sport) if s in available), None)


def merge(*layers: Iterable[Requested]) -> list[Requested]:
    """The layers in order (oldest first), one item per path. An explicit item is replaced only by
    a later explicit one; an inferred or default item never replaces an explicit one."""
    out: dict[str, Requested] = {}
    for layer in layers:
        for item in layer:
            held = out.get(item.path)
            if held is None or held.origin != "explicit" or item.origin == "explicit":
                out[item.path] = item
    return list(out.values())


def hard_limit(path: str, value: Any, limits: Limits, sport: str) -> str | None:
    """The reason code when an explicit ``value`` of ``path`` is beyond a hard limit."""
    if path == "sport" and value not in limits.sports:
        return "sport_not_offered"
    if (
        path == "length_min"
        and limits.max_class_minutes is not None
        and isinstance(value, int)
        and value > limits.max_class_minutes
    ):
        return "over_plan_length"
    if path.startswith("games."):
        if sport == "running":
            return "game_on_running"
        if not limits.games:
            return "games_not_in_plan"
        sec = value.get("duration_sec") if isinstance(value, Mapping) else None
        if isinstance(sec, int) and sec < MIN_GAME_SEC:
            return "game_under_10s"
    return None


def _value(brief: Brief, dumped: Mapping[str, Any], path: str) -> Any:
    if path.startswith("games."):
        return dumped["games"][int(path[6:])]
    return dumped.get(path, getattr(brief, path, None))


def _item(
    path: str, value: Any, origin: Origin, reason: str | None, quote: str | None
) -> InterpretationItem:
    return InterpretationItem(
        path=path, origin=origin, reason_code=reason, value=value, quote=quote
    )


def interpretation_for(
    brief: Brief,
    unmet: Iterable[Miss] = (),
    limits: Limits | None = None,
    quotes: Mapping[str, str] | None = None,
) -> list[InterpretationItem]:
    """How the brief was read. Nothing (an empty list) for a brief that states nothing and has no
    games, so every class of today is stored as before."""
    if not brief.explicit and not brief.games:
        return []
    limits = limits or Limits()
    missed = {m.path: m.code for m in unmet}
    dumped = brief.model_dump(mode="json", exclude_none=True)
    quotes = quotes or {}
    items: list[InterpretationItem] = []
    for path in brief.explicit:
        value = _value(brief, dumped, path)
        reason = hard_limit(path, value, limits, brief.sport) or missed.get(path)
        origin: Origin = "not_as_asked" if reason else "explicit"
        quote = keep_quote(quotes.get(path), brief.text)
        items.append(_item(path, value, origin, reason, quote))
    stated = set(brief.explicit)
    for path in _ASSUMED_FIELDS:
        value = _value(brief, dumped, path)
        if path in stated or value in (None, 0):
            continue
        default = Brief.model_fields[path].get_default(call_default_factory=True)
        items.append(_item(path, value, "default" if value == default else "inferred", None, None))
    for n, game in enumerate(dumped.get("games", [])):
        if f"games.{n}" not in stated:
            items.append(_item(f"games.{n}", game, "inferred", None, None))
    return items
