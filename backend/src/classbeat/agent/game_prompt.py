"""The games section of the cycling prompts (ADR 0029 section 3; pure text, no I/O).

The design, soundless and revise prompts share one block, so they name the same four games the
strict ``Game`` accepts and give the same guidance. The catalogue is rendered from
``classbeat.agent.games_catalog``. The running prompts never use this: running has no games and
its prompts stay free of game words (ADR 0018).

The 10 s rules are the library's (``workout.validate``); the prompt only tells the model to leave
a margin, because fitting to the music can shorten an interval by a few seconds.
"""

from typing import Final

from classbeat.agent import games_catalog

_BASE: Final = """\
Games (cycling only): an interval may carry a "game" field. The only types are these four, and \
you never write any other name:
{catalogue}
- A race (a burst of 15 to 35 s in zone 6 or 7) is not a game: never set "game" on a race, and \
never turn a race into a game on your own.
- Use a game only when the brief or the request asks for one. When it names a type, use exactly \
that type; when it asks for a game without a type (a team challenge, a game that rewards steady \
pacing), choose one of the four by its advice. Never invent a type; if the request names a game \
that is not one of the four, leave it out{left_out}.
- The interval's zone is the game's target. Most games reward higher team effort, so think about \
the load around a game and the effort the instructor asked for: put it where riders can give \
effort, not straight after the hardest work, and keep the class's overall load at the asked \
intensity.
- When the instructor rides on heart rate, prefer longer games: heart rate lags behind effort.
- A game lasts at least 10 s and so does the interval right after it. Leave a margin well over \
10 s{fit}.
- A cue may announce the coming game by name. There is no countdown before a game, so never \
promise one.
"""

_REVISE: Final = """\
- Editing games: add a game; replace an interval with a game (keeping its length and zone); \
change a game's type; remove a game; change a game's length; set a game's zone. Change only the \
interval the request names and keep every other interval, and every other game, as it is.
"""


def games_section(*, music: bool, revise: bool = False) -> str:
    """The prompt block; ``music`` adds the fitting margin, ``revise`` the editing operations."""
    fit = ", because fitting to the music can shorten an interval" if music else ""
    left_out = ", and say so in note" if revise else ""
    text = _BASE.format(catalogue=games_catalog.prompt_lines(), fit=fit, left_out=left_out)
    return text + _REVISE if revise else text
