"""The schema a **stored** draft is read with (ADR 0029 section 3, "Strict and stored schemas").

A ``class_revisions.content`` is immutable: a revision stored while a game was still offered keeps
it. ``StoredGame`` is the four games of the strict ``Game`` plus ``ghost_target``, which MOWL no
longer offers; everything else is the strict schema unchanged (the subclasses below only widen
``game``). Use it **only to read historical records**: history, compare, the change summary, the
revision APIs and the base of a revision job. The model-facing tools, ``to_spec`` for saving and
the approval of a Save or an Update (``classbeat.saving.approval``) use the strict
``classbeat.agent.schemas.draft_model``, and the actions that would write or reuse a legacy draft
refuse it first with ``game_retired`` (``classbeat.design.games_policy``).

``to_spec`` of a stored draft is safe to compute: it does not look up the library's ``GAMES``
(neither do ``workout.tss`` and ``workout.total_seconds``). ``workout.validate``,
``workout.interval_body`` and ``classbeat.mowl_state`` do, so a stored draft never reaches them.
A running draft has no games, so its stored schema is the strict ``RunningDraft``.
"""

from typing import Any, Literal

from pydantic import Field

from classbeat.agent.schemas import Game, Interval, RunningDraft, Segment, WorkoutDraft

# The strict four plus the retired Ghost Target (MOWL id 7), for reading only.
StoredGame = Game | Literal["ghost_target"]


# A redefined field replaces the parent's whole ``Field``, so its bounds are restated here; a test
# pins the stored JSON schema to the strict one apart from the game names.
class StoredInterval(Interval):
    game: StoredGame | None = None  # pyright: ignore[reportIncompatibleVariableOverride]


class StoredSegment(Segment):
    intervals: list[StoredInterval] = Field(min_length=1, max_length=60)  # pyright: ignore[reportIncompatibleVariableOverride]


class StoredWorkoutDraft(WorkoutDraft):
    segments: list[StoredSegment] = Field(min_length=1, max_length=20)  # pyright: ignore[reportIncompatibleVariableOverride]


StoredDraft = StoredWorkoutDraft | RunningDraft


def stored_draft_model(sport: str | None) -> type[StoredWorkoutDraft] | type[RunningDraft]:
    """The stored-record schema of a class's sport: ``running``, else cycling (absent is
    cycling)."""
    return RunningDraft if sport == "running" else StoredWorkoutDraft


def stored_draft(content: Any, sport: str | None) -> StoredDraft:
    """A stored ``class_revisions.content`` read with its class's sport's stored schema, never the
    other sport's (``ValidationError`` when it does not fit)."""
    return stored_draft_model(sport).model_validate(content)
