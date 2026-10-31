"""Helpers for the ``revise_songs`` tests (ADR 0008 section 6, issue SJ3). TEST ONLY, never the
network: the ``songenv`` world of ``songtools`` plus a class designed on picked songs and the
model scripts of a song change (phase 1 replacing slot 2, phase 2 the ``revise`` stage)."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from pickfake import call, entries, turn
from songtools import Rider, SongEnv, choose_turns, new_key

REVISE_KIND = "revise_songs"
REQUEST = "A harder song for the climb."
SCRIPTS = Path(__file__).parent / "agent_scripts"

# The 8 songs of ``songtools.CHOSEN``; slot 2 (a climb) is the one replaced.
_KEPT_REST = [(2, "recovery"), (3, "sprint"), (4, "sprint"), (5, "recovery"), (6, "cooldown")]


def replace_script(slot_two: int = 0) -> dict[str, Any]:
    """Phase 1 of a change of slot 2: ``beta`` finds tracks 10 to 19 (refs 0 to 9; 10 to 12 are
    kept songs, so the pool is refs 3 to 8), and pool ref ``slot_two`` takes the slot."""
    pool = entries(
        (3, "climb"), (4, "climb"), (5, "climb"), (6, "build"), (7, "build"), (8, "climb")
    )
    return {
        "model": "fake-model",
        "turns": [
            turn(call("search_tracks", {"query": "beta"}, expect={"ok": True})),
            turn(call("submit_pool", pool, expect={"accepted": True})),
            *choose_turns(
                [("kept", 0, "warmup"), ("kept", 1, "build"), ("pool", slot_two, "climb")]
                + [("kept", i, role) for i, role in _KEPT_REST]
            ),
        ],
    }


def revise_script() -> dict[str, Any]:
    """Phase 2's model: ``revise_good`` (it changes the first minutes)."""
    return json.loads((SCRIPTS / "revise_good.json").read_text(encoding="utf-8"))


def change_input(rider: Rider, class_id: object, base: object, **extra: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "class_id": str(class_id),
        "base_revision_id": str(base),
        "mode": "pick",
        "replace": [2],
        "request": REQUEST,
        "mowl_connection_id": str(rider.mowl_connection_id),
        "spotify_connection_id": str(rider.spotify_connection_id),
    }
    return {k: v for k, v in (values | extra).items() if v is not None}


async def change(rider: Rider, class_id: object, base: object, **extra: Any) -> httpx.Response:
    body = {"kind": REVISE_KIND, "input": change_input(rider, class_id, base, **extra)}
    return await rider.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


async def resume_change(
    rider: Rider, class_id: object, base: object, revision_id: object, **extra: Any
) -> httpx.Response:
    values: dict[str, Any] = {
        "mode": "resume",
        "resume": {"song_list_revision_id": str(revision_id)},
        "replace": None,
        "request": None,
    }
    return await change(rider, class_id, base, **(values | extra))


def current(songenv: SongEnv, class_id: UUID) -> UUID:
    return songenv.sql(
        "SELECT current_revision_id FROM classes WHERE id = :id", id=class_id
    ).scalar_one()


async def picked(songenv: SongEnv, rider: Rider) -> tuple[UUID, UUID, UUID]:
    """A class designed on picked songs: (class id, its draft, the approved song list)."""
    job_id, list_id = await songenv.approved(rider)
    row = await songenv.run(job_id)
    assert row.state == "completed", (row.state, row.error_category)
    return row.class_id, current(songenv, row.class_id), list_id


async def changed_waiting(
    songenv: SongEnv, rider: Rider, class_id: UUID, base: UUID, **extra: Any
) -> tuple[UUID, UUID, str]:
    """A song change run to its wait: (job id, new list id, its hash hex)."""
    songenv.script = replace_script()
    answer = await change(rider, class_id, base, **extra)
    assert answer.status_code == 201, answer.text
    job_id = UUID(answer.json()["id"])
    row = await songenv.run(job_id)
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    return job_id, row.approval_revision_id, bytes(row.approval_hash).hex()


async def changed_approved(
    songenv: SongEnv, rider: Rider, class_id: UUID, base: UUID
) -> tuple[UUID, UUID]:
    """A song change approved; the next run's model is ``revise_script``."""
    job_id, list_id, digest = await changed_waiting(songenv, rider, class_id, base)
    answer = await rider.approve(job_id, list_id, digest)
    assert answer.status_code == 200, answer.text
    songenv.script = revise_script()
    return job_id, list_id
