"""A studio output's state, derived from its external operations, and the action that is safe for
it (ADR 0036 section 6, ADR 0005 section 7, SR C7). Pure: no I/O.

**The mapping** (``derived``), over the operations of one attempt:

=====================================================  ======================================
an operation ``uncertain``, or ``pending`` while no    ``in_doubt``: it may have taken effect
job holds it (its holder died or lost its lease)
an operation ``pending`` that a running job holds      ``running``
an operation ``failed`` (a definite refusal, or the    ``failed``: safe to retry
request never left)
every step ``succeeded``                               ``done``
none yet                                               ``pending``
=====================================================  ======================================

``in_doubt`` wins over everything: it is never retried by a job and blocks a new write to that
output until the user's **Check again** settles it or **Create another anyway** (ADR 0005's Do it
anyway, as ``attempt + 1``) moves on. ``published_not_listed`` (a MOWL attach that found no mix by
its URL) is kept as stored.

**The actions** (``actions``), shown with the state: ``check_again`` for ``in_doubt`` (and for a
mix in doubt with a MOWL candidate found by its unique name also ``confirm``, the user's own
decision, never automatic) and for ``published_not_listed``; ``create_another`` for ``in_doubt``
(with the duplicate risk stated); ``retry`` (approve the outputs again) for ``failed``.
"""

from collections.abc import Iterable, Mapping
from typing import Any, Final

STATES: Final = ("pending", "running", "done", "failed", "in_doubt", "published_not_listed")
STEPS_OF: Final = {
    "spotify_playlist": ("spotify_create", "spotify_fill"),
    "swedebeat_playlist": ("create", "publish"),
    "swedebeat_mix": ("merge",),
    "mowl": ("attach",),
}


def derived(
    output: str,
    operations: Mapping[str, tuple[str, bool]],
    stored: str,
    *,
    job_open: bool,
) -> str:
    """The state of one attempt from ``operations`` (its step to ``(state, held)``, ``held`` when
    the operation is ``pending`` under a job that is still running), the ``stored`` state and
    whether a job of the class is still open (``job_open``)."""
    states = list(operations.values())
    if any(s == "uncertain" or (s == "pending" and not held) for s, held in states):
        return "in_doubt"
    if stored == "in_doubt":
        return "in_doubt"  # settled only by the user's Check again or Create another anyway
    if any(s == "pending" for s, _ in states):
        return "running"
    if all(operations.get(step, ("", False))[0] == "succeeded" for step in STEPS_OF[output]):
        return "done"
    if any(s == "failed" for s, _ in states):
        return "failed"
    if stored == "running" and not job_open:
        return "failed"  # its job ended between two steps: the next one resumes safely
    return stored if stored in STATES and stored != "done" else "pending"


def actions(state: str, *, candidate: bool = False) -> list[str]:
    """The user's safe actions for an output in ``state`` (see the module docstring)."""
    if state == "in_doubt":
        return ["check_again", *(["confirm"] if candidate else []), "create_another"]
    if state == "published_not_listed":
        return ["check_again"]
    if state == "failed":
        return ["retry"]
    return []


def note(output: str, state: str, steps: Iterable[str], detail: Mapping[str, Any]) -> str | None:
    """A code for the page's wording of a state, e.g. ``created_not_public`` (the playlist exists
    under its stored id and only the publish is open), ``may_exist`` (a create in doubt cannot be
    listed), ``not_sent`` (a mix that failed before it was sent)."""
    done = set(steps)
    if output == "swedebeat_playlist" and "create" in done and state != "done":
        return "created_not_public"
    if state == "in_doubt" and output in ("swedebeat_playlist", "spotify_playlist"):
        return "may_exist"
    if state == "in_doubt" and output == "swedebeat_mix":
        return "listed_candidate" if detail.get("candidate") else "may_exist"
    if state == "failed" and output == "swedebeat_mix":
        return "not_sent"
    return None
