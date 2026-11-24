"""Helpers for the composer's song fill, swap and change tests (ADR 0038 sections 5, 6 and 12,
#1188). TEST ONLY, never the network.

On ``songenv``'s world (the fake Spotify with an invented catalogue, ``FakeMowlPool``, the real
``design_with_songs`` kind and ``FakeProvider``), with ``composer_flow`` on: ``sketched`` stores a
composer class whose current revision is its sketch (as #1186's hand-off leaves it: a completed
``interpret_request`` job with the attached music, the stored interpretation with the pinned songs,
the class's brief and the sketch revision), and ``fill`` calls the hand-off route. The scripts mark
each turn with its stage (``only_in``), because every stage group gets a new provider: the pool and
the choice run on the ``songs`` group, the design on the ``design`` group (the ``revise`` stage).
Public test values only.
"""

import hashlib
import json
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest

from classbeat.agent.brief import Brief
from classbeat.design import composer
from classbeat.design.revision import canonical
from pickfake import LIST_8, POOL_14, call, entries, turn
from pickfake import songs as song_refs
from songtools import BRIEF, Rider, SongEnv

SCRIPTS = Path(__file__).parent / "agent_scripts"
REQUEST = "A 30 minute class with one climb and two sprints, please"


def _draft(name: str, key: str) -> dict[str, Any]:
    return json.loads((SCRIPTS / name).read_text(encoding="utf-8"))["drafts"][key]


SKETCH = _draft("soundless_first_draft.json", "good")  # 1800 s: eight song slots
REVISED = _draft("revise_good.json", "revised")  # the design on the songs, 1800 s


def staged(stage: str, turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**t, "only_in": stage} for t in turns]


def pool_turns(pool: list[tuple[int, str]] | None = None) -> list[dict[str, Any]]:
    picks = pool or POOL_14
    return staged(
        "pick_pool",
        [
            turn(
                call("search_tracks", {"query": "alpha"}, expect={"ok": True}),
                call("search_tracks", {"query": "beta"}, expect={"ok": True}),
            ),
            turn(call("check_pool", entries(*picks), expect={"ok": True, "valid": True})),
            turn(call("submit_pool", entries(*picks), expect={"accepted": True})),
        ],
    )


def choose_turns(listed: list[tuple[str, int, str]] | None = None) -> list[dict[str, Any]]:
    chosen = listed or LIST_8
    return staged(
        "choose_songs",
        [
            turn(call("get_pool", {}, expect={"ok": True})),
            turn(call("submit_song_list", song_refs(*chosen, note="Built to the climb."))),
        ],
    )


def design_turns(draft: str = "revised", note: str = "Timed to the songs.") -> list[dict[str, Any]]:
    return staged(
        "revise",
        [
            turn(call("get_music", {}, expect={"ok": True}, contains=['"tracks"'])),
            turn(
                call(
                    "submit_draft",
                    {"draft": {"$draft": draft}, "note": note},
                    expect={"accepted": True},
                )
            ),
        ],
    )


def fill_script(
    *, listed: list[tuple[str, int, str]] | None = None, design: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "model": "fake-model",
        "drafts": {"revised": REVISED, "long": long_draft()},
        "turns": [*pool_turns(), *choose_turns(listed), *(design or design_turns())],
    }


def change_script(draft: str = "revised") -> dict[str, Any]:
    """A change: only the design (no pick); ``long`` designs 40 minutes on 31 minutes of songs."""
    return {
        "model": "fake-model",
        "drafts": {"revised": REVISED, "long": long_draft()},
        "turns": design_turns(draft),
    }


def long_draft() -> dict[str, Any]:
    longer = json.loads(json.dumps(REVISED))
    longer["segments"][0]["intervals"].insert(
        0, {"sec": 600, "zone": 2, "rpm": "beat", "standing": False}
    )
    return longer


def digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


@dataclass
class ComposerEnv:
    songenv: SongEnv

    def sql(self, statement: str, **params: Any) -> Any:
        return self.songenv.sql(statement, **params)

    async def sketched(
        self,
        rider: Rider,
        *,
        sources: list[str] | None = None,
        pinned: list[dict[str, Any]] | None = None,
        decision: dict[str, Any] | None = None,
    ) -> tuple[UUID, UUID]:
        """A composer class whose current revision is its sketch: (class id, sketch id). With no
        ``decision`` the interpretation has no ``music_decision``, as a row stored before #1495."""
        owner = rider.user_id
        class_id, interpret_id, sketch_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        self.sql(
            "INSERT INTO classes (id, owner_id, title) VALUES (:c, :o, 'Composer class')",
            c=class_id,
            o=owner,
        )
        given: dict[str, Any] = {
            "request": REQUEST,
            "mowl_connection_id": str(rider.mowl_connection_id),
            "spotify_connection_id": str(rider.spotify_connection_id),
            "composer": True,
        }
        if sources:
            given["music_sources"] = [{"kind": "spotify_playlist", "id": s} for s in sources]
        self.sql(
            "INSERT INTO jobs (id, owner_id, kind, state, class_id, input, idempotency_key, "
            "request_hash, credit_state, finished_at) VALUES (:j, :o, 'interpret_request', "
            "'completed', :c, CAST(:i AS jsonb), :k, :h, 'none', now())",
            j=interpret_id,
            o=owner,
            c=class_id,
            i=json.dumps(given),
            k=secrets.token_urlsafe(18),
            h=digest("interpret"),
        )
        self.insert_sketch(owner, class_id, sketch_id, SKETCH, number=1)
        brief = Brief.model_validate(BRIEF).stored()
        interpretation: dict[str, Any] = {
            "v": 1,
            "job_id": str(interpret_id),
            "brief": brief,
            "music": {"source": "pick"},
            "items": [],
        }
        if pinned:
            interpretation["pinned_songs"] = pinned
        if decision is not None:
            interpretation["music_decision"] = decision
        self.sql(
            "UPDATE classes SET current_revision_id = :r, brief = CAST(:b AS jsonb), "
            "interpretation = CAST(:i AS jsonb) WHERE id = :c",
            r=sketch_id,
            b=json.dumps(brief),
            i=json.dumps(interpretation),
            c=class_id,
        )
        return class_id, sketch_id

    def insert_sketch(
        self, owner: UUID, class_id: UUID, sketch_id: UUID, content: dict[str, Any], *, number: int
    ) -> None:
        self.sql(
            "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
            "content_hash, music, provenance) VALUES (:r, :o, :c, :n, 'draft', "
            "CAST(:content AS jsonb), :h, CAST(:m AS jsonb), CAST(:p AS jsonb))",
            r=sketch_id,
            o=owner,
            c=class_id,
            n=number,
            content=json.dumps(content),
            h=hashlib.sha256(canonical(content)).digest(),
            m=json.dumps({"source": "sketch"}),
            p=json.dumps({"origin": "sketch"}),
        )

    async def fill(self, rider: Rider, class_id: UUID, *, retry: bool = False) -> httpx.Response:
        body = {"retry": True} if retry else None
        return await rider.browser.post(f"/api/classes/{class_id}/fill-songs", body)

    async def waiting(
        self, rider: Rider, class_id: UUID, script: dict[str, Any] | None = None
    ) -> tuple[UUID, UUID, str]:
        """A fill run to its wait: (job id, list id, content hash hex)."""
        made = await self.fill(rider, class_id)
        assert made.status_code == 201, made.text
        job_id = UUID(made.json()["job_id"])
        self.songenv.script = script or fill_script()
        row = await self.songenv.run(job_id)
        assert row.state == "waiting_for_approval", (row.state, row.error_category)
        return job_id, row.approval_revision_id, bytes(row.approval_hash).hex()

    def current(self, class_id: UUID) -> UUID | None:
        return self.sql(
            "SELECT current_revision_id FROM classes WHERE id = :c", c=class_id
        ).scalar()

    def drafts(self, class_id: UUID) -> int:
        return self.sql(
            "SELECT count(*) FROM class_revisions WHERE class_id = :c AND kind = 'draft'",
            c=class_id,
        ).scalar_one()

    def content(self, revision_id: UUID) -> dict[str, Any]:
        return self.sql(
            "SELECT content FROM class_revisions WHERE id = :r", r=revision_id
        ).scalar_one()


def composer_flag(songenv: SongEnv, value: Any) -> None:
    songenv.sql("DELETE FROM app_settings WHERE key = 'composer_flow'")
    if value is not None:
        songenv.sql(
            "INSERT INTO app_settings (key, value) VALUES ('composer_flow', CAST(:v AS jsonb))",
            v=json.dumps(value),
        )


@pytest.fixture
async def cenv(songenv: SongEnv) -> AsyncIterator[ComposerEnv]:
    composer.configure(composer.ComposerGate(enabled=True))
    composer_flag(songenv, "on")
    try:
        yield ComposerEnv(songenv)
    finally:
        composer.configure(None)
        composer_flag(songenv, None)


# The 8 songs of ``songtools.CHOSEN``; slot 2 (a climb) is the one swapped (``revisesongtools``).
_KEPT_REST = [(2, "recovery"), (3, "sprint"), (4, "sprint"), (5, "recovery"), (6, "cooldown")]


def swap_script(*, design: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A swap of slot 2: ``beta`` finds tracks 10 to 19 (refs 0 to 9; 10 to 12 are kept, so the
    pool is refs 3 to 8) and ref 0 of the pool takes the slot; then the design."""
    pool = entries(
        (3, "climb"), (4, "climb"), (5, "climb"), (6, "build"), (7, "build"), (8, "climb")
    )
    listed = [("kept", 0, "warmup"), ("kept", 1, "build"), ("pool", 0, "climb")]
    listed += [("kept", i, role) for i, role in _KEPT_REST]
    turns = [
        *staged(
            "pick_pool",
            [
                turn(call("search_tracks", {"query": "beta"}, expect={"ok": True})),
                turn(call("submit_pool", pool, expect={"accepted": True})),
            ],
        ),
        *choose_turns(listed),
        *(design or design_turns()),
    ]
    return {"model": "fake-model", "drafts": {"revised": REVISED}, "turns": turns}


async def swap(rider: Rider, class_id: UUID, list_id: UUID, *slots: int) -> httpx.Response:
    body = {"song_list_revision_id": str(list_id), "replace": list(slots or (2,))}
    key = {"Idempotency-Key": secrets.token_urlsafe(18)}
    return await rider.browser.post(f"/api/classes/{class_id}/swap-songs", body, key)
