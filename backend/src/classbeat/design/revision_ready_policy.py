"""Which revision origins are ready to be written anywhere (ADR 0038 section 4, #1186). Pure.

A revision's ``provenance.origin`` says how it was made. Every origin is classified here, once:

- **ready**: everything a user can save, update, send, duplicate, pick, refit or change today,
  and ``None`` (a revision stored before ADR 0007 section 2 recorded an origin; the history
  reads it as ``design_draft``);
- **not ready**: ``sketch`` only, the composer's design with no songs (ADR 0038 section 4). It is
  the class's current revision until phase 2 stores the first real draft, and it is never written
  to MOWL, Spotify or a player, never duplicated and never picked again.

An origin that is in neither set is not ready (fail closed). ``tests/test_revision_ready_guard.py``
enumerates every origin literal in the source and fails when one is in neither set.
"""

from typing import Final

NOT_READY: Final = "not_ready"  # the closed reason code of every refusal
SKETCH: Final = "sketch"

READY_ORIGINS: Final[frozenset[str | None]] = frozenset(
    {
        None,
        "design_draft",
        "revise_draft",
        "design_with_songs",
        "revise_songs",
        "studio_songs",
        "duplicate",
        "beat_one",
        "swedebeat_match",
    }
)
NOT_READY_ORIGINS: Final[frozenset[str]] = frozenset({SKETCH})


def is_ready(origin: str | None) -> bool:
    """Whether a revision of this origin may be written anywhere; an unknown origin may not."""
    return origin in READY_ORIGINS and origin not in NOT_READY_ORIGINS


def refusal(origin: str | None) -> str | None:
    """``not_ready`` for an origin that is not ready, else None."""
    return None if is_ready(origin) else NOT_READY
