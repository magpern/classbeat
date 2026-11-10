"""What the interpreter is sent (ADR 0029 section 4; pure: no network, no database).

An allowlist, built here and nowhere else, so the interpreter never sees more than it needs:

- the instructor's request, cleaned and cut;
- the plan's limits (longest class, games on or off) and the sports the account may use;
- the games catalogue (the four types, MOWL's one-line article; no classbeat advice);
- the instructor's playlists and Swedebeat mixes as one numbered list of cleaned names
  (``MAX_NAMES`` of them, ``MAX_NAME_CHARS`` characters each). The model answers with a position
  in that list, which ``InterpretedRequest.sanitised`` range-checks.

On a revise (ADR 0029 section 4, #904) the message also holds ``current``: the class's values as
they stand (``current_values``: the brief's closed numbers and enums and its games, never its text),
so a relative change ("ten minutes longer") is read as the value it leads to. A message without it
is byte-identical to before.

Never an id, a title, a token, a URL or a song. A name is the instructor's own text and is passed
as data in a JSON string, never joined into a sentence of the prompt.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final, NamedTuple

from classbeat.agent import games_catalog
from classbeat.agent.brief import Brief
from classbeat.agent.interpreted import MAX_NAMES
from classbeat.agent.schemas import clean_text

MAX_REQUEST_CHARS: Final = 2000
MAX_NAME_CHARS: Final = 60
NO_NAME: Final = "(no name)"


class MusicName(NamedTuple):
    """A playlist or a mix as the interpreter may see it: its kind and its name, nothing else."""

    kind: str  # "playlist" or "mix"
    name: str


def music_list(names: Sequence[MusicName]) -> list[dict[str, Any]]:
    """The numbered, cleaned list; the position is the only handle the model gets."""
    return [
        {"index": n, "kind": m.kind, "name": clean_text(m.name)[:MAX_NAME_CHARS] or NO_NAME}
        for n, m in enumerate(names[:MAX_NAMES])
    ]


def clean_request(request: str) -> str:
    """The request as sent, and so as a quote must match it."""
    return clean_text(request)[:MAX_REQUEST_CHARS]


def games_list() -> list[dict[str, str]]:
    return [{"type": g.type, "name": g.name, "article": g.article} for g in games_catalog.CATALOG]


# The closed values a revise's interpreter is told of the brief; never its text or explicit.
CURRENT_KEYS: Final = (
    "length_min",
    "intensity",
    "class_type",
    "climbs",
    "sprints",
    "races",
    "ftp_test",
    "games",
)


def current_values(brief: Brief) -> dict[str, Any]:
    """The class's values as they stand: closed numbers, enums and games only."""
    dumped = brief.model_dump(mode="json", exclude_none=True)
    return {key: dumped[key] for key in CURRENT_KEYS if key in dumped}


def first_message(
    request: str,
    *,
    sports: Sequence[str],
    max_class_minutes: int | None,
    games_allowed: bool,
    music: Sequence[MusicName],
    current: Mapping[str, Any] | None = None,
) -> str:
    body: dict[str, Any] = {
        "request": clean_request(request),
        "allowed_sports": list(sports),
        "plan_limits": {"max_class_minutes": max_class_minutes, "games": games_allowed},
        "games": games_list(),
        "music": music_list(music),
    }
    if current is not None:
        body["current"] = {k: current[k] for k in CURRENT_KEYS if k in current}
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)
