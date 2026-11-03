"""Duplicate a class (ADR 0007 amendment of 2026-11-03 part D, ADR 0011 amendment of the same
date; issue #547), against Postgres as ``classbeat_app`` through the web app. Fakes only: the fake
MOWL and ``FakeProvider`` for the revise and Save of a copy, and no fake is called by a duplicate.
"""

import asyncio
import copy
import hashlib
import json
import uuid
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.account.export import build_export
from classbeat.agent import Provenance
from classbeat.classes import duplicate as dup
from ctx import make_context
from jobapi import Browser
from jobtools import new_class
from revisetools import ReviseEnv
from savetools import DRAFT as SAVE_DRAFT
from savetools import RUNNING_BRIEF, RUNNING_DRAFT, Saved, SaveEnv

RUN = str(uuid.uuid4())
LINK = str(uuid.uuid4())
SONG_LIST = str(uuid.uuid4())
PLAYLIST_ID = "37i9dQZF1DXcBWIGoYBM5M"
BRIEF: dict[str, Any] = {"length_min": 20, "intensity": "moderate"}
# A valid cycling draft (15 minutes) without the game of ``savetools.DRAFT``; ``GAME_DRAFT`` has it.
GAME_DRAFT: dict[str, Any] = copy.deepcopy(SAVE_DRAFT)
DRAFT: dict[str, Any] = copy.deepcopy(SAVE_DRAFT)
for _segment in DRAFT["segments"]:
    for _interval in _segment["intervals"]:
        _interval.pop("game", None)
PROVENANCE: dict[str, Any] = {
    "provider": "fake",
    "model": "m",
    "model_answered": "m",
    "effort": "medium",
    "prompt_version": "a" * 64,
    "classbeat_version": "0.0.0",
    "mowl_version": "0.9.0",
    "map_version": 1,
    "crossfade_sec": 0.0,
    "agent_run_ids": [RUN],
    "usage": {
        "input_tokens": 5,
        "output_tokens": 6,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    },
    "cost_micros": 1234,
    "origin": "design_draft",
    "tss_approximate": False,
}
MUSIC: dict[str, Any] = {
    "source": "mowl_spotify_playlist",
    "fingerprint": "ab" * 32,
    "unknown_bpm": [],
}
NOT_FOUND = {"detail": {"reason": "not_found"}}
# Tables a duplicate must never write for the copy (item 6 of the ADR amendment).
NOT_COPIED = (
    "saved_workouts",
    "class_messages",
    "jobs",
    "external_operations",
    "class_playlists",
)


def digest(content: dict[str, Any]) -> bytes:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).digest()


def sql(conn: Connection, statement: str, **params: Any) -> Any:
    return conn.execute(text(statement), params)


class Source:
    """A user's class with a stored brief, a playlist music source and one draft (on approved
    songs: ``song_list_id`` set, so the copy must drop it)."""

    def __init__(
        self,
        admin: Connection,
        browser: Browser,
        *,
        brief: dict[str, Any] | None = BRIEF,
        content: dict[str, Any] | None = None,
        music_source: dict[str, Any] | None = None,
        music: dict[str, Any] | None = None,
    ) -> None:
        self.admin, self.browser, self.owner = admin, browser, browser.user_id
        self.content = copy.deepcopy(DRAFT if content is None else content)
        source = music_source or {
            "source": "mowl_spotify_playlist",
            "spotify_playlist_id": PLAYLIST_ID,
        }
        self.class_id = new_class(admin, self.owner)
        sql(
            admin,
            "UPDATE classes SET brief = CAST(:b AS jsonb), music_source = CAST(:m AS jsonb), "
            "dismissed_rules = '[\"intensity_mix\"]'::jsonb WHERE id = :c",
            b=None if brief is None else json.dumps(brief), m=json.dumps(source), c=self.class_id,
        )  # fmt: skip
        self.music = music or {
            "source": "mowl_spotify_playlist", "link_id": LINK, "fingerprint": "ab" * 32,
            "unknown_bpm": [], "song_list_id": SONG_LIST,
        }  # fmt: skip
        self.revision_id = sql(
            admin,
            "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
            "content_hash, music, provenance) VALUES (:id, :o, :c, 1, 'draft', "
            "CAST(:content AS jsonb), :h, CAST(:music AS jsonb), CAST(:p AS jsonb)) RETURNING id",
            id=uuid.uuid4(), o=self.owner, c=self.class_id, content=json.dumps(self.content),
            h=digest(self.content), music=json.dumps(self.music), p=json.dumps(PROVENANCE),
        ).scalar_one()  # fmt: skip
        sql(admin, "UPDATE classes SET current_revision_id = :r WHERE id = :c",
            r=self.revision_id, c=self.class_id)  # fmt: skip

    def body(self, **more: Any) -> dict[str, Any]:
        return {
            "revision_id": str(self.revision_id),
            "content_hash": digest(self.content).hex(),
            "title": "Tuesday climb (copy)",
            **more,
        }

    async def duplicate(self, class_id: UUID | str | None = None, **more: Any) -> httpx.Response:
        target = self.class_id if class_id is None else class_id
        return await self.browser.post(f"/api/classes/{target}/duplicate", self.body(**more))

    def classes(self) -> int:
        return sql(
            self.admin, "SELECT count(*) FROM classes WHERE owner_id = :o", o=self.owner
        ).scalar_one()

    def ledger(self) -> list[Any]:
        return sql(
            self.admin, "SELECT job_id, state, reason, settled_at FROM credit_ledger "
            "WHERE owner_id = :o", o=self.owner,
        ).all()  # fmt: skip

    def free_plan(self) -> None:
        """Revoke the test grant: the user is on ``free`` (30 minutes, no games, 3 classes)."""
        sql(
            self.admin,
            "UPDATE entitlements SET revoked_at = now() WHERE owner_id = :o",
            o=self.owner,
        )

    def hold(self, n: int) -> None:
        """``n`` committed classes this month, as a settled job's ledger rows look."""
        sql(
            self.admin,
            "INSERT INTO credit_ledger (owner_id, job_id, period, plan_id, state, settled_at) "
            "SELECT :o, NULL, date_trunc('month', now() AT TIME ZONE 'UTC')::date, 'free', "
            "'committed', now() FROM generate_series(1, :n)",
            o=self.owner, n=n,
        )  # fmt: skip

    def snapshot(self) -> list[Any]:
        """The source class and its rows: unchanged by anything done to the copy."""
        return [
            sql(self.admin, "SELECT c::text FROM classes c WHERE id = :c", c=self.class_id).all(),
            sql(
                self.admin,
                "SELECT r::text FROM class_revisions r WHERE class_id = :c ORDER BY number",
                c=self.class_id,
            ).all(),
        ]


@pytest.fixture
async def two(browsers: Any, jobs_admin: Connection) -> tuple[Source, Source]:
    a: Browser = await browsers(mowl=False)
    b: Browser = await browsers(mowl=False)
    return Source(jobs_admin, a), Source(jobs_admin, b)


def copy_rows(admin: Connection, class_id: str) -> tuple[Any, list[Any]]:
    found = sql(admin, "SELECT * FROM classes WHERE id = :c", c=class_id).one()
    revisions = sql(admin, "SELECT * FROM class_revisions WHERE class_id = :c", c=class_id).all()
    return found, revisions


# --- unit ---------------------------------------------------------------------------------------


def test_the_title_is_cleaned_and_bounded() -> None:
    assert dup.clean_title("  Tuesday\u0000 climb \n(copy) ") == "Tuesday climb (copy)"
    assert dup.clean_title("a​b") == "ab"  # a format character is dropped
    assert dup.clean_title(" \t ") is None
    assert dup.clean_title("x" * 101) is None
    assert dup.clean_title("x" * 100) == "x" * 100


def test_only_the_users_own_music_choice_is_copied() -> None:
    playlist = {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST_ID}
    mix = {"source": "mowl_swedebeat_mix", "mix_id": "mix-1"}
    picked = {**playlist, "origin": "song_pick", "class_playlist_id": str(uuid.uuid4())}
    assert dup.music_source_copy(playlist) == playlist
    assert dup.music_source_copy(mix) == mix
    assert dup.music_source_copy(picked) is None
    assert dup.music_source_copy({**playlist, "spotify_playlist_id": "x/../y"}) is None
    assert dup.music_source_copy(None) is None
    assert dup.music_copy({"link_id": LINK, "song_list_id": SONG_LIST}) == {"link_id": LINK}


def test_a_duplicate_provenance_copies_how_it_was_made_and_no_run_usage_or_cost() -> None:
    source, revision = uuid.uuid4(), uuid.uuid4()
    built = dup.provenance_copy(PROVENANCE, source, revision, 3)
    assert built["origin"] == "duplicate"
    assert built["duplicated_from"] == {
        "class_id": str(source), "revision_id": str(revision), "number": 3,
    }  # fmt: skip
    assert (built["agent_run_ids"], built["cost_micros"]) == ([], 0)
    assert set(built["usage"].values()) == {0}
    assert (built["model"], built["prompt_version"], built["tss_approximate"]) == (
        "m",
        "a" * 64,
        False,
    )
    assert built["base_revision_id"] is None
    # The model refuses a duplicate that copies a run or a cost, and a non-duplicate without a run.
    with pytest.raises(ValueError, match="duplicate"):
        Provenance.model_validate({**built, "agent_run_ids": [RUN]}, strict=False)
    with pytest.raises(ValueError, match="duplicate"):
        Provenance.model_validate({**built, "cost_micros": 1}, strict=False)
    with pytest.raises(ValueError, match="only a duplicate"):
        Provenance.model_validate({**PROVENANCE, "agent_run_ids": []}, strict=False)
    # Every other revision serializes exactly as before (no ``duplicated_from`` key).
    plain = Provenance.model_validate(PROVENANCE, strict=False).model_dump(mode="json")
    assert "duplicated_from" not in plain


# --- the API ------------------------------------------------------------------------------------


async def test_a_duplicate_is_a_new_class_with_revision_1_and_nothing_else(
    two: tuple[Source, Source],
) -> None:
    mine, _ = two
    before = mine.snapshot()
    got = await mine.duplicate(title="  My  copy\u0007 ")
    assert got.status_code == 201, got.text
    made = got.json()
    found, revisions = copy_rows(mine.admin, made["class_id"])
    assert (found.title, found.brief, found.owner_id) == ("My copy", BRIEF, mine.owner)
    assert found.music_source == {
        "source": "mowl_spotify_playlist",
        "spotify_playlist_id": PLAYLIST_ID,
    }
    assert found.dismissed_rules == []
    assert found.approved_swedebeat_match_id is None
    [rev] = revisions
    assert str(rev.id) == made["revision_id"] == str(found.current_revision_id)
    assert (rev.number, rev.kind, rev.parent_id, rev.job_id) == (1, "draft", None, None)
    assert (rev.content, bytes(rev.content_hash)) == (mine.content, digest(mine.content))
    assert rev.music == {k: v for k, v in mine.music.items() if k != "song_list_id"}
    assert rev.provenance["origin"] == "duplicate"
    assert rev.provenance["duplicated_from"] == {
        "class_id": str(mine.class_id), "revision_id": str(mine.revision_id), "number": 1,
    }  # fmt: skip
    assert (rev.provenance["agent_run_ids"], rev.provenance["cost_micros"]) == ([], 0)
    for table in NOT_COPIED:
        count = sql(mine.admin, f"SELECT count(*) FROM {table} WHERE class_id = :c",  # noqa: S608
                    c=made["class_id"]).scalar_one()  # fmt: skip
        assert count == 0, table
    # One class of the month: committed at once, with no job.
    [row] = mine.ledger()
    assert (row.job_id, row.state, row.reason) == (None, "committed", None)
    assert row.settled_at is not None
    assert mine.snapshot() == before  # the source is only read
    # The copy reads like any class.
    listed = (await mine.browser.get(f"/api/classes/{made['class_id']}")).json()
    assert (listed["current_revision_number"], listed["saved"]["count"]) == (1, 0)


async def test_a_class_on_picked_songs_is_copied_without_its_playlists(
    browsers: Any, jobs_admin: Connection
) -> None:
    picked = {
        "source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST_ID,
        "origin": "song_pick", "class_playlist_id": str(uuid.uuid4()),
    }  # fmt: skip
    mine = Source(jobs_admin, await browsers(mowl=False), music_source=picked)
    got = await mine.duplicate()
    assert got.status_code == 201, got.text
    found, [rev] = copy_rows(mine.admin, got.json()["class_id"])
    assert found.music_source is None
    assert "song_list_id" not in rev.music


async def test_another_users_class_or_revision_is_not_found_and_nothing_is_made(
    two: tuple[Source, Source],
) -> None:
    mine, theirs = two
    elsewhere = Source(mine.admin, mine.browser)  # another class of mine
    song_list = sql(
        mine.admin,
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance) VALUES (:id, :o, :c, 2, 'song_list', '{}'::jsonb, "
        ":h, '{}'::jsonb, '{}'::jsonb) RETURNING id",
        id=uuid.uuid4(), o=mine.owner, c=mine.class_id, h=digest({}),
    ).scalar_one()  # fmt: skip
    counts = (mine.classes(), theirs.classes())
    cases = [
        (theirs.class_id, theirs.body()),  # their class and revision
        (mine.class_id, theirs.body()),  # my class, their revision
        (theirs.class_id, mine.body()),  # their class, my revision
        (mine.class_id, elsewhere.body()),  # a revision of another class of mine
        (mine.class_id, mine.body(revision_id=str(song_list), content_hash=digest({}).hex())),
        (uuid.uuid4(), mine.body()),
        ("nope", mine.body()),
    ]
    for class_id, body in cases:
        got = await mine.browser.post(f"/api/classes/{class_id}/duplicate", body)
        assert (got.status_code, got.json()) == (404, NOT_FOUND), (class_id, body)
    assert (mine.classes(), theirs.classes()) == counts
    assert mine.ledger() == []
    assert theirs.ledger() == []


async def test_refusals_create_nothing(browsers: Any, jobs_admin: Connection) -> None:
    browser: Browser = await browsers(mowl=False)
    mine = Source(jobs_admin, browser)
    unready = Source(jobs_admin, browser, brief=None)
    running_music = {**MUSIC, "link_id": LINK}
    running = Source(
        jobs_admin, browser, brief=RUNNING_BRIEF, content=RUNNING_DRAFT, music=running_music
    )
    mix = Source(
        jobs_admin, browser, music_source={"source": "mowl_swedebeat_mix", "mix_id": "mix-1"}
    )
    classes = mine.classes()
    expected = [
        (await mine.duplicate(content_hash="0" * 64), 409, "hash_mismatch"),
        (await unready.duplicate(), 409, "not_ready"),
        (await running.duplicate(), 409, "feature_off"),  # running off in the test app
        (await mix.duplicate(), 409, "feature_off"),  # Swedebeat off in the test app
        (await mine.duplicate(title=" \u0000 "), 422, "invalid_input"),
        (await mine.duplicate(content_hash="XYZ"), 422, "invalid_input"),
        (await mine.duplicate(extra=1), 422, "invalid_input"),
    ]
    for got, status, reason in expected:
        assert (got.status_code, got.json()["detail"]["reason"]) == (status, reason), got.text
    assert mine.classes() == classes
    assert mine.ledger() == []


async def test_the_current_plan_gates_the_brief_and_the_draft(
    browsers: Any, jobs_admin: Connection
) -> None:
    browser: Browser = await browsers(mowl=False)
    long = Source(jobs_admin, browser, brief={"length_min": 45})
    with_game = Source(jobs_admin, browser, content=GAME_DRAFT)
    long.free_plan()
    classes = long.classes()
    got = await long.duplicate()
    assert got.status_code == 409
    assert got.json()["detail"] == {
        "reason": "quota", "quota": "length", "limit": 30, "used": 45, "plan": "free",
    }  # fmt: skip
    got = await with_game.duplicate()
    assert got.status_code == 409, got.text
    assert got.json()["detail"]["quota"] == "feature"
    assert got.json()["detail"]["feature"] == "games"
    assert long.classes() == classes
    assert long.ledger() == []


async def test_a_duplicate_takes_a_class_of_the_month(two: tuple[Source, Source]) -> None:
    mine, _ = two
    mine.free_plan()
    mine.hold(3)
    got = await mine.duplicate()
    assert got.status_code == 409
    detail = got.json()["detail"]
    assert (detail["reason"], detail["quota"], detail["limit"], detail["used"]) == (
        "quota", "classes", 3, 3,
    )  # fmt: skip
    assert "period_end" in detail
    assert len(mine.ledger()) == 3


async def test_parallel_duplicates_at_the_last_class_make_exactly_one(
    two: tuple[Source, Source],
) -> None:
    mine, _ = two
    mine.free_plan()
    mine.hold(2)
    classes = mine.classes()
    answers = await asyncio.gather(*(mine.duplicate(title=f"copy {n}") for n in range(10)))
    codes = sorted(a.status_code for a in answers)
    assert codes == [201] + [409] * 9, [a.text for a in answers]
    assert mine.classes() == classes + 1
    assert [r.state for r in mine.ledger()].count("committed") == 3


async def test_deleting_the_source_leaves_the_copy_and_the_copy_is_exported(
    two: tuple[Source, Source],
) -> None:
    mine, _ = two
    made = (await mine.duplicate()).json()
    deleted = await mine.browser.client.delete(
        f"/api/classes/{mine.class_id}", headers=await mine.browser.headers()
    )
    assert deleted.status_code == 200, deleted.text
    found, revisions = copy_rows(mine.admin, made["class_id"])
    assert found.current_revision_id is not None
    assert len(revisions) == 1
    document = await build_export(mine.browser.env.factory, make_context(mine.owner))
    exported = json.dumps(document, default=str)
    assert made["class_id"] in exported
    assert made["revision_id"] in exported


# --- independence through the real pipelines ----------------------------------------------------


async def test_revising_the_copy_leaves_the_source(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    source_revisions = renv.revisions(class_id)
    content = source_revisions[0].content
    body = {"revision_id": str(base), "content_hash": digest(content).hex(), "title": "Copy"}
    got = await renv.browser.post(f"/api/classes/{class_id}/duplicate", body)
    assert got.status_code == 201, got.text
    copy_id, copy_rev = UUID(got.json()["class_id"]), UUID(got.json()["revision_id"])
    job_id, _ = await renv.revise(copy_id, copy_rev, "revise_good")
    assert renv.denv.ops.row(job_id).state == "completed"
    assert [r.number for r in renv.revisions(copy_id)] == [1, 2]
    assert renv.current(copy_id) == renv.revisions(copy_id)[1].id
    assert renv.revisions(class_id) == source_revisions
    assert renv.current(class_id) == base
    assert renv.messages(class_id) == []


async def test_saving_the_copy_leaves_the_source_unsaved(senv: SaveEnv, save_browser: Any) -> None:
    saved = await senv.saved_class(brief={"length_min": 30})
    # Revisions are immutable to every role but the superuser that plants them: give the planted
    # one the provenance a design stores (a duplicate re-validates it).
    senv.sql(
        "UPDATE class_revisions SET provenance = CAST(:p AS jsonb) WHERE id = :r",
        p=json.dumps(PROVENANCE), r=saved.revision_id,
    )  # fmt: skip
    sb = await save_browser(saved)
    body = {"revision_id": str(saved.revision_id), "content_hash": saved.hash_hex, "title": "Copy"}
    got = await sb.browser.post(f"/api/classes/{saved.class_id}/duplicate", body)
    assert got.status_code == 201, got.text
    copied = Saved(
        saved.owner, saved.connection, UUID(got.json()["class_id"]),
        UUID(got.json()["revision_id"]), saved.link_id, saved.content,
    )  # fmt: skip
    made = await (await save_browser(copied)).save()
    assert made.status_code == 201, made.text
    await senv.run_next()
    assert senv.row(UUID(made.json()["id"])).state == "completed"
    rows = senv.saved_rows(saved.owner)
    assert [r.class_id for r in rows] == [copied.class_id]
    assert len(senv.fake.programs) == 1
    # Saving the source afterwards makes its own program; the copy's row is untouched.
    copy_row = rows[0]
    made = await sb.save()
    assert made.status_code == 201, made.text
    await senv.run_next()
    after = {r.class_id: r for r in senv.saved_rows(saved.owner)}
    assert set(after) == {saved.class_id, copied.class_id}
    assert after[copied.class_id].program_id == copy_row.program_id
