"""Legacy Ghost Target drafts are still readable (ADR 0029 section 3, MA1a-1; issue #877).

The strict schema offers the four games; a revision stored while Ghost Target was offered is read
with the stored schema (``classbeat.agent.stored_draft``) by every read path: the history, the
comparison, the change summary, the class's revision API and the job's revision API. The pure
parts run without a database; the API reads run against Postgres as ``classbeat_app`` (setup rows
planted as the superuser). No fake is called: these are reads.
"""

import copy
import hashlib
import json
import uuid
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.agent.schemas import Interval, WorkoutDraft, draft_model
from classbeat.agent.stored_draft import (
    StoredInterval,
    StoredWorkoutDraft,
    stored_draft,
    stored_draft_model,
)
from classbeat.design import diff, games_policy
from jobapi import Browser
from jobcore import JobsEnv
from jobtools import new_class

GAME_AT = (1, 0)  # (segment, interval) of the game in ``MODERN`` and ``LEGACY``
MODERN: dict[str, Any] = {
    "name": "Team night",
    "description": "",
    "segments": [
        {
            "name": "Warm up",
            "kind": "warmup",
            "intervals": [{"sec": 300, "zone": 2, "rpm": [85, 95], "standing": False}],
        },
        {
            "name": "Main",
            "kind": "main",
            "intervals": [
                {"sec": 120, "zone": 5, "rpm": [95, 110], "standing": False, "game": "fast_lane"},
                {"sec": 60, "zone": 2, "rpm": [85, 95], "standing": False},
            ],
        },
        {
            "name": "Cool down",
            "kind": "cooldown",
            "intervals": [{"sec": 180, "zone": 1, "rpm": [80, 90], "standing": False}],
        },
    ],
}


def legacy(content: dict[str, Any] = MODERN, game: str = "ghost_target") -> dict[str, Any]:
    """``content`` with its game interval's game replaced by ``game`` (as stored before MA1a)."""
    out = copy.deepcopy(content)
    segment, interval = GAME_AT
    out["segments"][segment]["intervals"][interval]["game"] = game
    return out


LEGACY = legacy()
MUSIC: dict[str, Any] = {"link_id": str(uuid.uuid4()), "fingerprint": "ab" * 32, "unknown_bpm": []}


def digest(content: dict[str, Any]) -> bytes:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).digest()


def plant(
    admin: Connection,
    owner: UUID,
    class_id: UUID,
    number: int,
    content: dict[str, Any],
    *,
    origin: str = "design_draft",
    job_id: UUID | None = None,
) -> UUID:
    """A ``draft`` revision inserted directly, as a revision stored before MA1a would be."""
    return admin.execute(
        text(
            "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
            "content_hash, music, provenance, job_id) VALUES (:id, :o, :c, :n, 'draft', "
            "CAST(:content AS jsonb), :h, CAST(:music AS jsonb), CAST(:p AS jsonb), :j) "
            "RETURNING id"
        ),
        {
            "id": uuid.uuid4(), "o": owner, "c": class_id, "n": number,
            "content": json.dumps(content), "h": digest(content), "music": json.dumps(MUSIC),
            "p": json.dumps({"origin": origin, "cost_micros": 0}), "j": job_id,
        },
    ).scalar_one()  # fmt: skip


# --- the schemas and the policy (pure) ------------------------------------------------------------


def test_the_strict_schema_refuses_ghost_target_and_the_stored_one_reads_it() -> None:
    with pytest.raises(ValidationError):
        WorkoutDraft.model_validate(LEGACY)
    with pytest.raises(ValidationError):
        draft_model(None).model_validate(LEGACY)
    read = stored_draft(LEGACY, None)
    assert isinstance(read, StoredWorkoutDraft)
    assert read.to_spec()["segments"][1]["intervals"][0]["game"] == "ghost_target"
    assert stored_draft_model("cycling") is StoredWorkoutDraft
    for unknown in ("poker", "Ghost Target", 7):  # never any other name
        with pytest.raises(ValidationError):
            stored_draft(legacy(game=unknown), None)  # pyright: ignore[reportArgumentType]


def test_the_stored_schema_is_the_strict_one_apart_from_the_game_names() -> None:
    def schema(model: type[WorkoutDraft]) -> str:
        text = json.dumps(model.model_json_schema(), sort_keys=True)
        return text.replace("StoredInterval", "Interval").replace("StoredSegment", "Segment")

    strict, stored = schema(WorkoutDraft), schema(StoredWorkoutDraft)
    ghost = '{"const": "ghost_target", "type": "string"}, '
    assert ghost not in strict
    assert stored.replace(ghost, "").replace("StoredWorkoutDraft", "WorkoutDraft") == strict
    assert set(StoredInterval.model_fields) == set(Interval.model_fields)


def test_the_policy_names_only_games_outside_the_four_and_reads_any_shape() -> None:
    assert games_policy.RETIRED == "game_retired"
    assert {"racetrack", "power_pull", "fast_lane", "overflow"} == games_policy.OFFERED
    assert games_policy.has_retired_game(LEGACY)
    assert games_policy.has_retired_game(legacy(game="unknown_game"))
    assert not games_policy.has_retired_game(MODERN)
    for game in sorted(games_policy.OFFERED):
        assert not games_policy.has_retired_game(legacy(game=game))
    no_game = copy.deepcopy(MODERN)
    del no_game["segments"][1]["intervals"][0]["game"]
    running = {"segments": [{"intervals": [{"sec": 60, "zone": 2, "gradient": 0}]}]}
    for content in (no_game, running, {}, None, [], {"segments": "x"}, {"segments": [1, {}]}):
        assert not games_policy.has_retired_game(content)


def test_the_change_summary_compares_a_legacy_draft_without_looking_up_games() -> None:
    change = diff.summarize(LEGACY, MODERN)
    assert change["intervals"] == {"added": 0, "removed": 0, "changed": 1}  # only the game
    assert change["duration_sec"] == {"before": 660, "after": 660}
    assert change["tss"]["before"] == change["tss"]["after"]
    assert diff.summarize(LEGACY, LEGACY)["intervals"]["changed"] == 0


# --- the class API: history, compare and the revision -------------------------------------------


class Legacy:
    """A user's class with a legacy draft (r1) and a modern one (r2, current)."""

    def __init__(self, admin: Connection, browser: Browser) -> None:
        self.browser, owner = browser, browser.user_id
        self.class_id = new_class(admin, owner)
        self.r1 = plant(admin, owner, self.class_id, 1, LEGACY)
        self.r2 = plant(admin, owner, self.class_id, 2, MODERN, origin="revise_draft")
        admin.execute(
            text("UPDATE classes SET current_revision_id = :r WHERE id = :c"),
            {"r": self.r2, "c": self.class_id},
        )


@pytest.fixture
async def mine(browsers: Any, jobs_admin: Connection) -> Legacy:
    return Legacy(jobs_admin, await browsers(mowl=False))


async def test_the_history_lists_a_legacy_revision_as_readable(mine: Legacy) -> None:
    got = await mine.browser.get(f"/api/classes/{mine.class_id}/history")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["unreadable"] == 0
    first, second = body["revisions"]
    assert (first["number"], second["number"]) == (1, 2)
    assert first["summary"] == {"segments": 3, "intervals": 4, "total_sec": 660}
    assert first["content_hash"] == digest(LEGACY).hex()
    assert isinstance(first["tss"], float)


async def test_compare_reads_a_legacy_revision_on_either_side(mine: Legacy) -> None:
    base = f"/api/classes/{mine.class_id}/history/compare"
    for older, newer in ((mine.r1, mine.r2), (mine.r2, mine.r1), (mine.r1, mine.r1)):
        got = await mine.browser.get(f"{base}?from={older}&to={newer}")
        assert got.status_code == 200, got.text
        changed = 0 if older == newer else 1
        assert got.json()["intervals"] == {"added": 0, "removed": 0, "changed": changed}
        # The compare route words the game change too (issue #878): segment counted from 1.
        games = (
            []
            if older == newer
            else [{"segment": 2, "before": "ghost_target", "after": "fast_lane"}]
        )
        if older == mine.r2 and newer == mine.r1:
            games = [{"segment": 2, "before": "fast_lane", "after": "ghost_target"}]
        assert got.json()["game_changes"] == games


async def test_the_revision_api_shows_a_legacy_draft_with_its_game(mine: Legacy) -> None:
    got = await mine.browser.get(f"/api/classes/{mine.class_id}/revisions/{mine.r1}")
    assert got.status_code == 200, got.text
    content = got.json()["content"]
    assert content["segments"][1]["intervals"][0]["game"] == "ghost_target"
    listed = await mine.browser.get(f"/api/classes/{mine.class_id}/revisions")
    assert listed.status_code == 200
    assert {r["number"] for r in listed.json()["revisions"]} == {1, 2}


# --- the job's revision API -----------------------------------------------------------------------


@pytest.mark.usefixtures("jenv")
async def test_the_jobs_revision_api_shows_a_legacy_draft(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    job_id = (await alice.create()).json()["id"]
    row = jenv.row(UUID(job_id))
    plant(jenv.admin, row.owner_id, row.class_id, 1, LEGACY, job_id=UUID(job_id))
    got = await alice.get(f"/api/jobs/{job_id}/revision")
    assert got.status_code == 200, got.text
    assert got.json()["content"]["segments"][1]["intervals"][0]["game"] == "ghost_target"
