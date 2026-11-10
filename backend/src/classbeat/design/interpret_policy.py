"""From what the interpreter read to the brief and the music a design job takes (ADR 0029 sections
4 and 5; pure: no database, no network, no model).

``resolve`` applies ``request_policy``'s rules to an ``InterpretedRequest``:

- an **explicit** value is kept, unless a hard limit stops it: then the brief takes the nearest
  allowed value (the plan's longest class; a game held to MOWL's 10 s floor) or leaves it out (a
  sport that is not offered falls back to cycling; a game on a running class or a plan without
  games; a cycling-only knob of a running class; races or an FTP test the plan does not include),
  and the item is **Not as asked** with its reason code and the value that was asked for. A game
  is never swapped for another. Only an explicit value that is kept goes into ``Brief.explicit``;
- an **inferred** value is kept the same way and is Assumed; one beyond a limit is held to it
  (or left out) quietly, since nobody asked for it;
- a value the model left out takes the app's default (45 minutes, moderate, cycling), Assumed;
- ``intensity`` and ``class_type`` stated explicitly go into ``Brief.explicit`` (the model is told
  to keep them) but are recorded as Assumed, as ``request_policy.UNVERIFIED`` says: nothing
  checks them against the draft, so the page never shows them under "As you asked" (the
  ``stated_unchecked`` decision of ADR 0029 section 4, settled here for the page's catalogue);
- the music as ``interpret_music_policy.choose`` picks it.

Every value an item carries is closed (numbers,
short enums, a game as a small object, the music's source); a quote is kept only if it is a
substring of the request. Nothing a model wrote as text reaches the brief.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from classbeat.agent.brief import Brief, BriefGame
from classbeat.agent.explicit_check import MIN_GAME_SEC
from classbeat.agent.interpret_input import clean_request
from classbeat.agent.interpretation_record import InterpretationItem, Origin, keep_quote
from classbeat.agent.interpreted import InterpretedRequest, Reading
from classbeat.design import request_policy
from classbeat.design.interpret_music_policy import Music, MusicOption, choose
from classbeat.plan_gates import PlanGates

DEFAULT_LENGTH_MIN: Final = 45
_KNOBS: Final = ("length_min", "intensity", "class_type", "climbs", "sprints", "races", "ftp_test")
_CYCLING_ONLY: Final = ("climbs", "sprints", "races", "ftp_test")
TITLE_CHARS: Final = 60


@dataclass(frozen=True)
class Offer:
    """What the account may use: the sports, the plan's gates, the music and whether a song pick
    can run (song picking admits the user and a Spotify connection is named)."""

    sports: tuple[str, ...] = ("cycling",)
    gates: PlanGates | None = None
    options: tuple[MusicOption, ...] = ()
    pick_ready: bool = False


@dataclass(frozen=True)
class Resolved:
    brief: Brief
    music: Music | None  # None: a running class with no MOWL playlist
    items: list[InterpretationItem] = field(default_factory=lambda: list[InterpretationItem]())


def _item(
    path: str, origin: Origin, value: Any, reason: str | None = None, quote: str | None = None
) -> InterpretationItem:
    return InterpretationItem(
        path=path, origin=origin, reason_code=reason, value=value, quote=quote
    )


def _held(path: str, value: Any, sport: str, offer: Offer) -> tuple[Any, str | None]:
    """The value the brief may take (None: left out) and the reason when it is not the one read."""
    gates = offer.gates
    limits = request_policy.Limits.from_gates(gates, offer.sports)
    reason = request_policy.hard_limit(path, value, limits, sport)
    if reason == "over_plan_length":
        return limits.max_class_minutes, reason
    ftp = path == "ftp_test" or (path == "class_type" and value == "ftp_test")
    if sport == request_policy.ALL_SPORTS[1] and (path in _CYCLING_ONLY or ftp):
        return None, "cycling_only"
    if gates is not None and path == "races" and value and not gates.races:
        return None, "plan_no_races"
    if gates is not None and ftp and not gates.ftp_tests:
        return None, "plan_no_ftp_tests"
    return value, None


def _sport(reading: Reading | None, offer: Offer, items: list[InterpretationItem]) -> str:
    if reading is None:
        items.append(_item("sport", "default", "cycling"))
        return "cycling"
    if reading.value not in offer.sports:
        if reading.origin == "explicit":
            items.append(_item("sport", "not_as_asked", reading.value, "sport_not_offered"))
        return "cycling"
    items.append(_item("sport", reading.origin, reading.value, None, reading.quote))
    return str(reading.value)


def _games(
    read: InterpretedRequest, request: str, sport: str, offer: Offer, out: list[InterpretationItem]
) -> tuple[list[BriefGame], list[str]]:
    limits = request_policy.Limits.from_gates(offer.gates, offer.sports)
    games: list[BriefGame] = []
    explicit: list[str] = []
    for n, wanted in enumerate(read.games):
        path, game = f"games.{n}", wanted.brief_game()
        value = game.model_dump(mode="json", exclude_none=True)
        reason = request_policy.hard_limit(path, value, limits, sport)
        quote = keep_quote(wanted.quote, request)
        if reason in ("game_on_running", "plan_no_games"):  # every game alike: none is kept
            if wanted.origin == "explicit":
                out.append(_item(path, "not_as_asked", value, reason, quote))
            continue
        if reason == "game_under_10s":  # the nearest allowed length, never another game
            game = game.model_copy(update={"duration_sec": MIN_GAME_SEC})
            if wanted.origin == "explicit":
                out.append(_item(path, "not_as_asked", value, reason, quote))
        else:
            out.append(_item(path, wanted.origin, value, None, quote))
        games.append(game)
        if wanted.origin == "explicit":
            explicit.append(path)
    return games, explicit


def resolve(
    read: InterpretedRequest,
    text: str,
    offer: Offer,
    *,
    sport_override: str | None = None,
    music_override: Mapping[str, str] | None = None,
) -> Resolved:
    """The brief, the music and the items of one interpreted request (the module's rules)."""
    request = clean_request(text)
    readings = {r.path: r for r in read.readings()}
    if sport_override is not None:  # the user's own choice on the page: stated, never guessed
        readings["sport"] = Reading("sport", sport_override, "explicit", None)
    items: list[InterpretationItem] = []
    sport = _sport(readings.get("sport"), offer, items)
    fields: dict[str, Any] = {"text": request}
    if sport != "cycling":
        fields["sport"] = sport
    explicit = ["sport"] if readings.get("sport") and items[-1].origin == "explicit" else []
    for path in _KNOBS:
        held = readings.get(path)
        if held is None:
            continue
        value, reason = _held(path, held.value, sport, offer)
        quote = keep_quote(held.quote, request)
        if reason is not None and held.origin == "explicit":
            items.append(_item(path, "not_as_asked", held.value, reason, quote))
        elif value is not None:
            unchecked = held.origin == "explicit" and path in request_policy.UNVERIFIED
            items.append(_item(path, "inferred" if unchecked else held.origin, value, None, quote))
        if value is not None:
            fields[path] = value
            if held.origin == "explicit" and reason is None:
                explicit.append(path)
    if "length_min" not in fields:
        cap = offer.gates.max_class_minutes if offer.gates is not None else DEFAULT_LENGTH_MIN
        fields["length_min"] = min(DEFAULT_LENGTH_MIN, cap)
        items.append(_item("length_min", "default", fields["length_min"]))
    if "intensity" not in fields:
        items.append(_item("intensity", "default", "moderate"))
    games, stated_games = _games(read, request, sport, offer, items)
    if games:
        fields["games"] = games
    fields["explicit"] = explicit + stated_games
    override = dict(music_override or {}) or None
    music = choose(read.music, request, sport, offer.options, offer.pick_ready, override, items)
    return Resolved(Brief.model_validate(fields), music, items)


def title_for(request: str) -> str:
    """A class title from the request when the user gave none: its first words, cut at a space,
    at most ``TITLE_CHARS`` characters. Never a model's text."""
    words = " ".join(clean_request(request).split())
    if len(words) <= TITLE_CHARS:
        return words
    cut = words[: TITLE_CHARS - 1].rsplit(" ", 1)[0] or words[: TITLE_CHARS - 1]
    return cut + "…"


def worst_case_brief(text: str) -> Brief:
    """The largest brief ``resolve`` can make of this request (every knob, six games at a relative
    placement, every path explicit): the request is admitted only if this one fits the class."""
    game = {
        "type": "any",
        "placement": {"relation": "replaces", "anchor": "interval", "ordinal": 20},
        "duration_sec": 600,
    }
    knobs = {"climbs": 10, "sprints": 20, "races": 5, "ftp_test": 20, "class_type": "journey"}
    explicit = ["sport", *_KNOBS, *(f"games.{n}" for n in range(6))]
    return Brief.model_validate(
        {
            "length_min": 180,
            "intensity": "moderate",
            **knobs,
            "text": clean_request(text),
            "games": [game] * 6,
            "explicit": explicit,
        }
    )
