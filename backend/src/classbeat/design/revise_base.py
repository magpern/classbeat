"""The base a ``revise_draft`` run revises (ADR 0007 section 3; ADR 0029 section 3), split out of
``classbeat.design.revise``.

``load`` is step 1 of the pipeline: one fenced read of the class's brief, music source and base
(``classbeat.design.chat.load_base``, through the job's own class); the base's hash checked against
the one ``admit`` stored and against its content; the brief, the music source and the base parsed.
The base is read with the **stored** schema (``classbeat.agent.stored_draft``), so a draft stored
while Ghost Target was offered still loads.

**A legacy base** (``classbeat.design.games_policy``): before the model sees it, every interval
with a retired game becomes a regular interval of the same length and zone (its game removed,
nothing else changed), and the run records a ``game_retired`` Not as asked item in the revision's
change summary (``noted``). The model is never asked to pick a replacement game, and the draft it
gets is the strict schema, so no retired game reaches a model or the stored result.
"""

import hashlib
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from classbeat.agent import Brief
from classbeat.agent.schemas import AnyDraft, RunningDraft, WorkoutDraft
from classbeat.agent.stored_draft import StoredDraft, stored_draft
from classbeat.design import chat, revision
from classbeat.design.games_policy import RETIRED, is_retired
from classbeat.design.kind import (
    CLASS_MUSIC_SOURCE,
    ClassSpotifyMusic,
    NoMusicChoice,
    SwedebeatMusicChoice,
)
from classbeat.jobs import JobFailure, JobRun

# The change summary's key for what the revision did not do as asked (MA1b-1 moves it to the
# interpretation record). A list of reason codes only, so the summary stays within its 1024 bytes.
NOT_AS_ASKED: Final = "not_as_asked"
# The store's compare-and-set: the new revision becomes current only if the base still is.
CAS: Final = text(
    "UPDATE classes c SET current_revision_id = :revision_id, updated_at = now() FROM jobs j "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND c.owner_id = j.owner_id "
    "AND c.id = j.class_id AND c.current_revision_id = :base RETURNING c.id"
)


@dataclass(frozen=True, slots=True)
class RunBase:
    """The checked base of a run: ``draft`` is what the model may see (strict, no retired game);
    ``retired`` says a retired game was turned into a regular interval."""

    row: chat.BaseForRun
    base_hash: bytes
    brief: Brief
    music: ClassSpotifyMusic | SwedebeatMusicChoice | NoMusicChoice
    draft: AnyDraft
    retired: bool


def without_retired_games(stored: StoredDraft) -> tuple[AnyDraft, bool]:
    """The strict draft with every retired game removed (same ``sec`` and ``zone``), and whether
    one was. A running draft has no games and is returned as it is."""
    if isinstance(stored, RunningDraft):
        return stored, False
    data = stored.model_dump(mode="python")
    retired = False
    for segment in data["segments"]:
        for interval in segment["intervals"]:
            if is_retired(interval.get("game")):
                interval["game"] = None
                retired = True
    return WorkoutDraft.model_validate(data), retired


def noted(change: dict[str, Any], retired: bool) -> dict[str, Any]:
    """The change summary with the ``game_retired`` Not as asked item when a game was retired."""
    return {**change, NOT_AS_ASKED: [RETIRED]} if retired else change


async def load(job_run: JobRun, base_id: UUID, base_hash_hex: str, context: int) -> RunBase:
    """Step 1 of the run: ``invalid_input`` for a base that is not a draft of a class with a brief
    and a music source, ``internal`` for one that no longer checks."""
    async with job_run.fenced() as fs:
        row = await chat.load_base(fs.db, fs.lease.owner_id, fs.lease.job_id, base_id, context)
    if row is None or row.kind != "draft" or not row.brief or not row.music_source:
        raise JobFailure("invalid_input")
    base_hash = bytes.fromhex(base_hash_hex)
    if (
        row.content_hash != base_hash
        or hashlib.sha256(revision.canonical(row.content)).digest() != base_hash
    ):
        raise JobFailure("internal")  # a revision is immutable: the stored hash must hold
    try:
        brief = Brief.model_validate(row.brief)
        music = CLASS_MUSIC_SOURCE.validate_python(row.music_source)
        draft, retired = without_retired_games(stored_draft(row.content, brief.sport))
    except ValidationError:
        raise JobFailure("internal") from None
    return RunBase(row, base_hash, brief, music, draft, retired)
