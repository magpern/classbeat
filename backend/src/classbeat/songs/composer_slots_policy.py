"""The song slots of a composer's sketch and where its pinned songs go (ADR 0038 section 5, #1188).
Pure: no database, no network, no model.

- **Slots** (``slot_roles``): the sketch's length decides how many songs (``n`` = its seconds over
  ``SONG_SEC``, 1 to ``MAX_SONGS``, as ``PickPlan`` does for a brief), and each slot's role is the
  sketch's segment at the slot's middle: a ``warmup`` or ``cooldown`` segment is that role, a
  ``recovery`` one ``recovery``, and a ``main`` one by its interval there (standing at zone 4 or
  more: ``climb``; zone 6 or more and at most 60 s: ``sprint``; zone 5 or more: ``interval``;
  else ``build``). The first slot is always an opener and the last a ``cooldown`` (the song list's
  own rule).
- **Pinned songs** (``pin_slots``): each pin's ``at`` is the reading's position hint ("warm-up",
  "climb 3", "last climb", "song 2"; cleaned text). It names a role (with a number or ``first``
  or ``last``) or a song number; the slot is that one if it is free, else the nearest free slot.
  A hint that names nothing goes to the first free slot between the first and the last. A pin
  never moves another pin.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from classbeat.agent.schemas import MAX_SONGS
from classbeat.agent.songs import SONG_SEC

ROLE_WORDS: Final[Mapping[str, str]] = {
    "warmup": "warmup",
    "warm": "warmup",
    "cooldown": "cooldown",
    "cool": "cooldown",
    "climb": "climb",
    "hill": "climb",
    "sprint": "sprint",
    "interval": "interval",
    "race": "race",
    "recovery": "recovery",
    "build": "build",
}
_NUMBER = re.compile(r"\b(\d{1,2})\b")
_SONG = re.compile(r"\b(?:song|track|slot)\s*(\d{1,2})\b")


def intervals_of(content: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    """``(segment kind, interval)`` in play order."""
    out: list[tuple[str, Mapping[str, Any]]] = []
    for segment in content.get("segments") or []:
        for interval in segment.get("intervals") or []:
            out.append((str(segment.get("kind")), interval))
    return out


def sketch_seconds(content: Mapping[str, Any]) -> int:
    return sum(int(i.get("sec") or 0) for _, i in intervals_of(content))


def song_count(seconds: int) -> int:
    return min(MAX_SONGS, max(1, int(seconds / SONG_SEC + 0.5)))


def _role(kind: str, interval: Mapping[str, Any]) -> str:
    if kind in ("warmup", "cooldown", "recovery"):
        return kind
    zone, sec = int(interval.get("zone") or 0), int(interval.get("sec") or 0)
    if zone >= 6 and sec <= 60:
        return "sprint"
    if interval.get("standing") is True and zone >= 4:
        return "climb"
    return "interval" if zone >= 5 else "build"


def slot_roles(content: Mapping[str, Any]) -> list[str]:
    """One role per song slot of the sketch (module docstring)."""
    timeline = intervals_of(content)
    total = sketch_seconds(content)
    n = song_count(total)
    roles: list[str] = []
    for slot in range(n):
        middle = (slot + 0.5) * total / n
        at, role = 0, "build"
        for kind, interval in timeline:
            at += int(interval.get("sec") or 0)
            if middle < at:
                role = _role(kind, interval)
                break
        roles.append(role)
    if roles[0] not in ("warmup", "build"):
        roles[0] = "warmup"
    if n > 1:
        roles[-1] = "cooldown"
    return roles


def _wanted(at: str | None, roles: Sequence[str]) -> int | None:
    """The slot a hint names, or None."""
    if not at:
        return None
    words = at.casefold().replace("-", "").replace("_", " ")
    song = _SONG.search(words)
    if song is not None:
        index = int(song.group(1)) - 1
        return index if 0 <= index < len(roles) else None
    role = next((r for w, r in ROLE_WORDS.items() if w in words), None)
    if role is None:
        return None
    of_role = [i for i, r in enumerate(roles) if r == role]
    if not of_role:
        return {"warmup": 0, "cooldown": len(roles) - 1}.get(role)
    if "last" in words or "final" in words:
        return of_role[-1]
    number = _NUMBER.search(words)
    if number is not None:
        index = int(number.group(1)) - 1
        return of_role[index] if 0 <= index < len(of_role) else of_role[-1]
    return of_role[0]


def pin_slots(hints: Sequence[str | None], roles: Sequence[str]) -> list[int | None]:
    """The slot of each pin, in the order given; None when no slot is left."""
    taken: set[int] = set()
    out: list[int | None] = []
    middle = list(range(1, len(roles) - 1)) or list(range(len(roles)))
    for hint in hints:
        wanted = _wanted(hint, roles)
        if wanted is None:
            free = [s for s in middle if s not in taken]
        else:
            near: int = wanted
            free = sorted(
                (s for s in range(len(roles)) if s not in taken), key=lambda s: (abs(s - near), s)
            )
        slot = free[0] if free else None
        if slot is not None:
            taken.add(slot)
        out.append(slot)
    return out
