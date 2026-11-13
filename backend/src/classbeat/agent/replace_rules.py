"""The replace-mode rules of a song list (ADR 0008 section 6): the list keeps every slot, every
kept song stays, and a replaced slot is filled from the pool. Pure: no I/O. Split out of
``choose_songs``."""

from collections.abc import Sequence

from classbeat.agent.schemas import SongRef
from classbeat.agent.songs import PickPlan


def replace_rules(plan: PickPlan, songs: Sequence[SongRef], kept_at: dict[int, int]) -> list[str]:
    """The errors of a list in replace mode; none in pick mode."""
    if not plan.replace:
        return []
    errors = []
    if len(songs) != plan.list_length:
        errors.append(f"the list keeps every slot: it needs {plan.list_length} songs")
    for i in range(len(plan.kept)):
        if i not in kept_at:
            errors.append(f"kept song {i} is missing; every kept song stays")
    kept_slots = {k.slot for k in plan.kept}
    for n, s in enumerate(songs):
        if n not in kept_slots and s.source != "pool":
            errors.append(f"song {n}: a replaced slot is filled from the pool")
    return errors
