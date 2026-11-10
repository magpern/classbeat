"""What the interpreter is sent (ADR 0029 section 4; pure: no network, no database).

An allowlist, built here and nowhere else, so the interpreter never sees more than it needs:

- the instructor's request, cleaned and cut;
- the plan's limits (longest class, games on or off) and the sports the account may use;
- the games catalogue (the four types, MOWL's one-line article; no classbeat advice);
- the instructor's playlists and Swedebeat mixes as one numbered list of cleaned names
  (``MAX_NAMES`` of them, ``MAX_NAME_CHARS`` characters each). The model answers with a position
  in that list, which ``InterpretedRequest.sanitised`` range-checks.

Never an id, a title, a token, a URL or a song. A name is the instructor's own text and is passed
as data in a JSON string, never joined into a sentence of the prompt.
"""

import json
from collections.abc import Sequence
from typing import Any, Final, NamedTuple

from classbeat.agent import games_catalog
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


def first_message(
    request: str,
    *,
    sports: Sequence[str],
    max_class_minutes: int | None,
    games_allowed: bool,
    music: Sequence[MusicName],
) -> str:
    return json.dumps(
        {
            "request": clean_request(request),
            "allowed_sports": list(sports),
            "plan_limits": {"max_class_minutes": max_class_minutes, "games": games_allowed},
            "games": games_list(),
            "music": music_list(music),
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )
