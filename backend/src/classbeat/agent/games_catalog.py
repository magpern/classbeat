"""The four MOWL games classbeat offers (ADR 0029 section 2 and 3; pure: no database, network or
I/O).

One row per game of the Session Designer, from MOWL's Help Center article "How to add a GAME"
(ADR 0029 section 2, G1): its i18n key (the page's name for it), its MOWL id (looked up from the
library's ``GAMES``, never typed here), the article's one-line description, and classbeat's own
advice. The advice is **ours, not MOWL's**: every place that shows it says so. The facts the
article states about licence (G2), heart rate (G6) and the missing countdown (G9) sit beside the
rows. The 10 s rules (G3, G4) live only in the library's ``workout.validate``.

It lives in ``classbeat.agent`` and not in ``classbeat.design``: the prompts use it at import time,
and importing the ``design`` package from ``agent`` is a cycle (``design.kind`` imports the agent).
The page and the other games code read it from here too.

``prompt_lines`` renders the catalogue for the design and revise prompts, so the model sees the
same four names the strict ``Game`` accepts and nothing else.
"""

from dataclasses import dataclass
from typing import Final, get_args

from mowl import workout

from classbeat.agent.schemas import Game


@dataclass(frozen=True)
class GameInfo:
    type: Game
    name: str  # the English name the article uses (the page translates ``i18n_key``)
    i18n_key: str
    mowl_id: int  # IntervalGame.IntervalGameID
    article: str  # MOWL's one-line description (G1)
    advice: str  # classbeat's advice, labelled as advice wherever it is shown


def _row(type_: Game, name: str, article: str, advice: str) -> GameInfo:
    return GameInfo(type_, name, f"class.game.{type_}", workout.GAMES[type_], article, advice)


CATALOG: Final[tuple[GameInfo, ...]] = (
    _row(
        "racetrack",
        "Racetrack",
        "Teams complete the most laps before time runs out.",
        "a sustained competitive push",
    ),
    _row(
        "power_pull",
        "Power Pull",
        "Two teams compete on power-to-weight ratio (W/kg), like a tug of war.",
        "fair across body sizes; suits climbs",
    ),
    _row(
        "fast_lane",
        "Fast Lane",
        "Teams compete for the highest average speed.",
        "a flat, fast cadence",
    ),
    _row(
        "overflow",
        "Overflow",
        "Teams earn progress by staying in their target intensity zones.",
        "steady control; suits mixed groups",
    ),
)
TYPES: Final[tuple[Game, ...]] = get_args(Game)
BY_TYPE: Final[dict[str, GameInfo]] = {g.type: g for g in CATALOG}

# G2, G6 and G9: facts of the article, not rules classbeat checks.
LICENCE: Final = (
    "Games are a commercial feature: they need a studio with a commercial MOWL licence. They are "
    "added in the desktop Session Designer, never during a session, and the instructor can switch "
    "them off before class. classbeat does not check any of this."
)
HEART_RATE: Final = (
    "Games work with power and with heart rate; longer games are recommended for heart rate, "
    "which lags behind effort."
)
NO_COUNTDOWN: Final = (
    "There is no countdown before a game; the workout overview shows which game comes next."
)


def label(game: str) -> str:
    """The English name of a game type; a type outside the catalogue (a retired game) as it is."""
    found = BY_TYPE.get(game)
    return found.name if found else game


def prompt_lines() -> str:
    """The catalogue as the prompts print it: one line per game, the advice marked as ours."""
    return "\n".join(
        f'- "{g.type}" ({g.name}): {g.article} (classbeat advice, not MOWL fact: {g.advice})'
        for g in CATALOG
    )
