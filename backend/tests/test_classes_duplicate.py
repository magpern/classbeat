"""Duplicate a class (ADR 0007 amendment of 2026-11-03 part D, ADR 0011 amendment of the same
date; issue #547), against Postgres as ``classbeat_app`` through the web app. Fakes only: the fake
MOWL and ``FakeProvider`` for the revise and Save of a copy, and no fake is called by a duplicate.
"""

import asyncio
import copy
import hashlib
import json
import uuid
from collections.abc import Callable, Iterator
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.account.export import build_export
from classbeat.agent import Provenance
from classbeat.classes import duplicate as dup
from classbeat.design import music_kind, sport
from ctx import make_context
from jobapi import Browser, new_key, owner_lock_key, until_waiting_on_the_owner_lock
from jobcore import JobsEnv
from jobtools import engine_for, new_class
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


async def post_duplicate(
    browser: Browser, class_id: UUID | str, body: dict[str, Any], key: str | None = None
) -> httpx.Response:
    """``POST .../duplicate`` with an ``Idempotency-Key`` (a fresh one unless given)."""
    headers = {"Idempotency-Key": key or new_key()}
    return await browser.post(f"/api/classes/{class_id}/duplicate", body, headers)


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

    async def duplicate(
        self, class_id: UUID | str | None = None, *, key: str | None = None, **more: Any
    ) -> httpx.Response:
        target = self.class_id if class_id is None else class_id
        return await post_duplicate(self.browser, target, self.body(**more), key)

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
    assert dup.clean_title("Tuesday\nclimb") == "Tuesday climb"  # a line break is a space
    assert dup.clean_title("Tuesday\tclimb\r\nhard") == "Tuesday climb hard"
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
    key, request = "k" * 16, "c" * 64
    built = dup.provenance_copy(
        PROVENANCE, source, revision, 3, idempotency_key=key, request_hash=request
    )
    assert built["origin"] == "duplicate"
    assert built["duplicated_from"] == {
        "class_id": str(source), "revision_id": str(revision), "number": 3,
        "idempotency_key": key, "request_hash": request,
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
    source = rev.provenance["duplicated_from"]
    assert {k: source[k] for k in ("class_id", "revision_id", "number")} == {
        "class_id": str(mine.class_id), "revision_id": str(mine.revision_id), "number": 1,
    }  # fmt: skip
    assert source["request_hash"] == dup.request_digest(
        mine.class_id, mine.revision_id, digest(mine.content), "My copy"
    )
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
        got = await post_duplicate(mine.browser, class_id, body)
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


async def test_a_retried_request_makes_one_class_and_takes_one_credit(
    two: tuple[Source, Source],
) -> None:
    mine, theirs = two
    key = new_key()
    first = await mine.duplicate(key=key)
    again = await mine.duplicate(key=key)
    assert (first.status_code, again.status_code) == (201, 200), again.text
    assert again.json() == first.json()
    assert len(mine.ledger()) == 1
    other = await mine.duplicate(key=key, title="Another name")
    assert (other.status_code, other.json()) == (
        409, {"detail": {"reason": "idempotency_mismatch"}},
    )  # fmt: skip
    # Keys are per user: the other user's same key makes their own copy.
    theirs_first = await theirs.duplicate(key=key)
    assert theirs_first.status_code == 201
    assert theirs_first.json()["class_id"] != first.json()["class_id"]
    assert len(mine.ledger()) == 1
    assert len(theirs.ledger()) == 1


async def test_the_idempotency_key_is_required(two: tuple[Source, Source]) -> None:
    mine, _ = two
    path = f"/api/classes/{mine.class_id}/duplicate"
    missing = await mine.browser.post(path, mine.body())
    bad = await mine.browser.post(path, mine.body(), {"Idempotency-Key": "short"})
    assert (missing.status_code, missing.json()["detail"]["reason"]) == (
        400, "idempotency_key_missing",
    )  # fmt: skip
    assert (bad.status_code, bad.json()["detail"]["reason"]) == (400, "idempotency_key_invalid")
    assert mine.ledger() == []


async def test_a_running_class_is_admitted_only_through_the_running_gate(
    browsers: Any, jobs_admin: Connection
) -> None:
    """#509 (ADR 0018 Amendment 5): a running copy goes through ``sport.admit_running``, as a
    running design does: the switch, a stored ``on`` (nothing stored is off) and the user's own
    plan covered (``running_not_evaluated``), before anything is written."""
    mine = Source(
        jobs_admin,
        await browsers(mowl=False),
        brief=RUNNING_BRIEF,
        content=RUNNING_DRAFT,
        music={**MUSIC, "link_id": LINK},
    )
    classes = mine.classes()

    def gate(*, enabled: bool = True, fake: bool = True, stored: bool = True) -> None:
        sport.configure(sport.SportGate(running_enabled=enabled, fake_covers=fake))
        sql(jobs_admin, "DELETE FROM app_settings WHERE key = 'running_enabled'")
        if stored:
            sql(jobs_admin, "INSERT INTO app_settings (key, value) "
                "VALUES ('running_enabled', '\"on\"'::jsonb)")  # fmt: skip

    try:
        for setting, reason in (
            ({"enabled": False}, "feature_off"),  # the environment's switch
            ({"stored": False}, "feature_off"),  # nothing stored: off since #509
            ({"fake": False}, "running_not_evaluated"),  # the user's own plan has no route
        ):
            gate(**setting)
            got = await mine.duplicate()
            assert (got.status_code, got.json()["detail"]["reason"]) == (409, reason), setting
        assert mine.classes() == classes
        assert mine.ledger() == []
        gate()  # all three hold
        made = await mine.duplicate()
        assert made.status_code == 201, made.text
    finally:
        sport.configure(None)
        sql(jobs_admin, "DELETE FROM app_settings WHERE key = 'running_enabled'")


NONE: dict[str, str] = {"source": "none"}


@pytest.fixture
def soundless(jobs_admin: Connection) -> Iterator[Callable[..., None]]:
    """``set(enabled=, fake=)``: the soundless gate (#527) of the web app under test, as
    ``test_soundless_jobs`` sets it (no stored admin flag since #824); cleared afterwards."""

    def configure(*, enabled: bool = True, fake: bool = True) -> None:
        music_kind.configure(music_kind.SoundlessGate(enabled=enabled, fake_covers=fake))

    try:
        yield configure
    finally:
        music_kind.configure(None)


def soundless_source(admin: Connection, browser: Browser) -> Source:
    return Source(admin, browser, music_source=NONE, music=dict(NONE))


async def test_a_soundless_class_is_refused_while_the_gate_is_off(
    browsers: Any, jobs_admin: Connection, soundless: Callable[..., None]
) -> None:
    mine = soundless_source(jobs_admin, await browsers(mowl=False))
    classes = mine.classes()
    for setting, reason in (
        ({"enabled": False}, "feature_off"),  # the environment's switch
        ({"fake": False}, "soundless_not_evaluated"),  # the user's own plan has no covering route
    ):
        soundless(**setting)
        got = await mine.duplicate()
        assert (got.status_code, got.json()["detail"]["reason"]) == (409, reason), setting
    assert mine.classes() == classes
    assert mine.ledger() == []


async def test_a_soundless_copy_stays_soundless_and_its_revise_follows_the_gate(
    renv: ReviseEnv, soundless: Callable[..., None]
) -> None:
    """A soundless copy keeps ``{source: "none"}`` (class and revision), and a revise of it goes
    through ``music_kind.admit_class`` like any soundless revise (SL2): the gate's refusal, with
    nothing written, while the gate is closed; accepted once it is open."""
    mine = soundless_source(renv.denv.ops.admin, renv.browser)
    soundless()
    made = await mine.duplicate()
    assert made.status_code == 201, made.text
    copy_id, copy_rev = UUID(made.json()["class_id"]), UUID(made.json()["revision_id"])
    found, [rev] = copy_rows(mine.admin, str(copy_id))
    assert (found.music_source, rev.music) == (NONE, NONE)

    def written() -> tuple[int, int]:
        jobs = renv.sql("SELECT count(*) FROM jobs WHERE class_id = :c", c=copy_id).scalar_one()
        return jobs, len(renv.messages(copy_id))

    for setting, reason in (
        ({"enabled": False}, "feature_off"),  # the environment's switch
        ({"fake": False}, "soundless_not_evaluated"),  # the user's own plan is not covered
    ):
        soundless(**setting)
        asked = await renv.ask(copy_id, copy_rev)
        assert (asked.status_code, asked.json()["detail"]["reason"]) == (409, reason), setting
        assert written() == (0, 0), setting
    soundless()  # the gate open again
    asked = await renv.ask(copy_id, copy_rev)
    assert asked.status_code == 201, asked.text
    assert written() == (1, 1)
    found, _ = copy_rows(mine.admin, str(copy_id))
    assert found.music_source == NONE  # the kind never changes


async def test_a_copy_without_music_is_refused_by_revise_before_anything_is_written(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    # The class's songs were picked: its music source names its own playlists (ADR 0008).
    picked = {
        "source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST_ID,
        "origin": "song_pick", "class_playlist_id": str(uuid.uuid4()),
    }  # fmt: skip
    renv.sql("UPDATE classes SET music_source = CAST(:m AS jsonb) WHERE id = :c",
             m=json.dumps(picked), c=class_id)  # fmt: skip
    content = renv.revisions(class_id)[0].content
    body = {"revision_id": str(base), "content_hash": digest(content).hex(), "title": "Copy"}
    made = await post_duplicate(renv.browser, class_id, body)
    assert made.status_code == 201, made.text
    copy_id, copy_rev = UUID(made.json()["class_id"]), UUID(made.json()["revision_id"])
    asked = await renv.ask(copy_id, copy_rev)
    assert (asked.status_code, asked.json()["detail"]["reason"]) == (409, "not_changeable")
    assert renv.messages(copy_id) == []
    jobs = renv.sql("SELECT count(*) FROM jobs WHERE class_id = :c", c=copy_id).scalar_one()
    assert jobs == 0


async def test_parallel_requests_with_one_key_wait_behind_the_owner_lock_and_make_one_class(
    jenv: JobsEnv, two: tuple[Source, Source], database_url: str
) -> None:
    """D item 7 under the owner lock: while another transaction holds the user's lock, every
    request waits, so the race between the key lookup and the insert happens on every run; once
    the lock is released exactly one class and one credit exist."""
    mine, _ = two
    key = new_key()
    classes = mine.classes()
    with engine_for(database_url) as engine, engine.connect() as holder:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": owner_lock_key(mine.owner)},
        )
        tasks = [asyncio.create_task(mine.duplicate(key=key)) for _ in range(5)]
        await until_waiting_on_the_owner_lock(jenv)
        assert not any(t.done() for t in tasks)
        holder.commit()
        answers = await asyncio.gather(*tasks)
    assert sorted(a.status_code for a in answers) == [200, 200, 200, 200, 201]
    assert len({a.json()["class_id"] for a in answers}) == 1
    assert mine.classes() == classes + 1
    assert len(mine.ledger()) == 1


async def test_the_owner_lock_is_taken_before_the_period_lock(
    jenv: JobsEnv, two: tuple[Source, Source], database_url: str
) -> None:
    """ADR 0011's order, as job creation: while another transaction holds the user's period row,
    the duplicate waits on it already holding its owner lock (the other order could deadlock
    against a job create)."""
    mine, _ = two
    with engine_for(database_url) as engine, engine.connect() as holder:
        holder.execute(
            text(
                "INSERT INTO quota_periods (owner_id, period) VALUES "
                "(:o, date_trunc('month', now() AT TIME ZONE 'UTC')::date) "
                "ON CONFLICT (owner_id, period) DO NOTHING"
            ),
            {"o": mine.owner},
        )
        holder.execute(
            text(
                "SELECT id FROM quota_periods WHERE owner_id = :o "
                "AND period = date_trunc('month', now() AT TIME ZONE 'UTC')::date FOR UPDATE"
            ),
            {"o": mine.owner},
        )
        task = asyncio.create_task(mine.duplicate())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        while not jenv.sql(
            "SELECT count(*) FROM pg_stat_activity WHERE usename = 'classbeat_app' "
            "AND wait_event_type = 'Lock' AND wait_event <> 'advisory'"
        ).scalar_one():
            assert loop.time() < deadline, "the duplicate never waited for the period row"
            await asyncio.sleep(0.02)
        assert not task.done()
        held = jenv.sql(
            "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid, "
            "(SELECT hashtextextended(:k, 0) AS h) x WHERE a.usename = 'classbeat_app' "
            "AND l.locktype = 'advisory' AND l.granted AND l.objsubid = 1 "
            "AND l.classid::bigint = ((x.h >> 32) & 4294967295) "
            "AND l.objid::bigint = (x.h & 4294967295)",
            k=owner_lock_key(mine.owner),
        ).scalar_one()
        assert held == 1, "the duplicate waits for the period row without the owner lock"
        holder.commit()
        answer = await task
    assert answer.status_code == 201, answer.text


async def test_a_mix_on_the_chosen_revision_is_refused_while_swedebeat_is_off(
    browsers: Any, jobs_admin: Connection
) -> None:
    """D item 2, the stricter rule: the class's current source is a playlist, but the chosen
    revision was fitted to a Swedebeat mix (before a hand-off back, say); the flag is off."""
    mix_music = {
        "source": "mowl_swedebeat_mix", "mix_id": "mix-1", "name": "Mix", "duration": 900.0,
        "listed_duration": 900.0, "unsure_beat_one": [], "fingerprint": "cd" * 32,
        "link_id": LINK,
    }  # fmt: skip
    mine = Source(jobs_admin, await browsers(mowl=False), music=mix_music)
    classes = mine.classes()
    got = await mine.duplicate()
    assert (got.status_code, got.json()["detail"]["reason"]) == (409, "feature_off")
    assert mine.classes() == classes
    assert mine.ledger() == []


async def test_changing_the_source_leaves_the_copy_and_account_deletion_removes_it(
    two: tuple[Source, Source],
) -> None:
    mine, _ = two
    made = (await mine.duplicate()).json()
    before = copy_rows(mine.admin, made["class_id"])
    sql(mine.admin, "UPDATE classes SET title = 'Renamed', current_revision_id = NULL "
        "WHERE id = :c", c=mine.class_id)  # fmt: skip
    assert copy_rows(mine.admin, made["class_id"]) == before
    # The account's deletion cascades to the copy, as to every class (ADR 0014).
    sql(mine.admin, "DELETE FROM users WHERE id = :o", o=mine.owner)
    for table in ("classes", "class_revisions", "credit_ledger"):
        left = sql(mine.admin, f"SELECT count(*) FROM {table} WHERE owner_id = :o",  # noqa: S608
                   o=mine.owner).scalar_one()  # fmt: skip
        assert left == 0, table


# --- independence through the real pipelines ----------------------------------------------------


async def test_revising_the_copy_leaves_the_source(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    source_revisions = renv.revisions(class_id)
    content = source_revisions[0].content
    body = {"revision_id": str(base), "content_hash": digest(content).hex(), "title": "Copy"}
    got = await post_duplicate(renv.browser, class_id, body)
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
    got = await post_duplicate(sb.browser, saved.class_id, body)
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


# --- beat-one corrections (DU3, #585; the beat-one amendment's item 8) --------------------------

FP = "ab" * 32  # the fingerprint of ``Source``'s default music
OTHER_FP = "ef" * 32


def put_correction(
    admin: Connection, owner: UUID, class_id: UUID, song: int, beats: int,
    *, fingerprint: str = FP, bpm: float = 128.0,
) -> UUID:  # fmt: skip
    return sql(
        admin,
        "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
        "VALUES (:o, :c, :f, :s, :b, :bpm) RETURNING id",
        o=owner, c=class_id, f=fingerprint, s=song, b=beats, bpm=bpm,
    ).scalar_one()  # fmt: skip


def corrections(admin: Connection, class_id: UUID | str) -> list[Any]:
    return sql(
        admin,
        "SELECT id, owner_id, class_id, fingerprint, song, beats, bpm FROM beat_one_corrections "
        "WHERE class_id = :c ORDER BY fingerprint, song",
        c=class_id,
    ).all()


def owned_corrections(admin: Connection, owner: UUID) -> int:
    return sql(
        admin, "SELECT count(*) FROM beat_one_corrections WHERE owner_id = :o", o=owner
    ).scalar_one()


async def pending(browser: Browser, class_id: object) -> bool:
    """BC4's ``pending`` of the class detail, read through the class API (the real computation):
    the rows for the current revision's fingerprint differ from what it applied."""
    got = await browser.get(f"/api/classes/{class_id}")
    assert got.status_code == 200, got.text
    block = got.json()["beat_one"]
    assert block["available"], block
    return block["pending"]


BEAT_ONE = [{"song": 1, "beats": 2}, {"song": 3, "beats": -1}]
SONGS = [
    {"song": n, "start": 240.0 * (n - 1), "end": 240.0 * n, "bpm": bpm, "estimate": False}
    for n, bpm in ((1, 124.5), (2, 128.0), (3, 130.0))
]


def with_beat_one(music: dict[str, Any] | None = None) -> dict[str, Any]:
    base = music or {
        "source": "mowl_spotify_playlist", "link_id": LINK, "fingerprint": FP,
        "unknown_bpm": [], "song_list_id": SONG_LIST,
    }  # fmt: skip
    return {**base, "beat_one": BEAT_ONE, "songs": SONGS}


def test_only_a_copy_with_music_gets_corrections_and_only_for_its_fingerprint() -> None:
    playlist = {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST_ID}
    mix = {"source": "mowl_swedebeat_mix", "mix_id": "mix-1"}
    music = {"source": "mowl_spotify_playlist", "fingerprint": FP}
    assert dup.corrections_fingerprint(playlist, music) == FP
    assert dup.corrections_fingerprint(mix, {**music, "source": "mowl_swedebeat_mix"}) == FP
    assert dup.corrections_fingerprint(None, music) is None  # picked songs: no music source
    assert dup.corrections_fingerprint(dict(NONE), music) is None  # a soundless copy
    assert dup.corrections_fingerprint(playlist, dict(NONE)) is None  # soundless revision music
    assert dup.corrections_fingerprint(playlist, {"source": "mowl_spotify_playlist"}) is None
    assert dup.corrections_fingerprint(playlist, {**music, "fingerprint": "AB" * 32}) is None
    assert dup.corrections_fingerprint(playlist, None) is None


async def test_a_copy_gets_the_corrections_for_the_chosen_revisions_music_and_nothing_else(
    two: tuple[Source, Source],
) -> None:
    mine, theirs = two
    mine = Source(mine.admin, mine.browser, music=with_beat_one())
    elsewhere = Source(mine.admin, mine.browser)  # another class of mine, on the same music
    put_correction(mine.admin, mine.owner, mine.class_id, 1, 2, bpm=124.5)
    put_correction(mine.admin, mine.owner, mine.class_id, 3, -1, bpm=130.0)
    put_correction(mine.admin, mine.owner, mine.class_id, 2, 3, fingerprint=OTHER_FP)  # stale
    put_correction(mine.admin, mine.owner, elsewhere.class_id, 4, 1)  # another class of mine
    put_correction(theirs.admin, theirs.owner, theirs.class_id, 5, -2)  # another user's class
    source_rows = corrections(mine.admin, mine.class_id)
    theirs_before = owned_corrections(theirs.admin, theirs.owner)
    got = await mine.duplicate()
    assert got.status_code == 201, got.text
    copy_id = got.json()["class_id"]
    copied = corrections(mine.admin, copy_id)
    assert [(r.fingerprint, r.song, r.beats, r.bpm) for r in copied] == [
        (FP, 1, 2, 124.5), (FP, 3, -1, 130.0),
    ]  # fmt: skip
    assert {(r.owner_id, str(r.class_id)) for r in copied} == {(mine.owner, copy_id)}
    assert not {r.id for r in copied} & {r.id for r in source_rows}, "new ids"
    # The copy's revision keeps what was applied, so its corrections read as applied.
    _, [rev] = copy_rows(mine.admin, copy_id)
    assert rev.music["beat_one"] == BEAT_ONE
    assert await pending(mine.browser, mine.class_id) is False
    assert await pending(mine.browser, copy_id) is False
    # The source, the other class and the other user are only read.
    assert corrections(mine.admin, mine.class_id) == source_rows
    assert len(corrections(mine.admin, elsewhere.class_id)) == 1
    assert owned_corrections(theirs.admin, theirs.owner) == theirs_before


async def test_an_older_revision_gets_the_corrections_for_its_own_fingerprint(
    browsers: Any, jobs_admin: Connection
) -> None:
    """The chosen revision's fingerprint, not the current revision's: revision 2 (current) is on
    other music; duplicating revision 1 copies revision 1's rows."""
    mine = Source(jobs_admin, await browsers(mowl=False))
    newer = {**mine.music, "fingerprint": OTHER_FP}
    newer_id = sql(
        jobs_admin,
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance, parent_id) VALUES (:id, :o, :c, 2, 'draft', "
        "CAST(:content AS jsonb), :h, CAST(:music AS jsonb), CAST(:p AS jsonb), :parent) "
        "RETURNING id",
        id=uuid.uuid4(), o=mine.owner, c=mine.class_id, content=json.dumps(mine.content),
        h=digest(mine.content), music=json.dumps(newer), p=json.dumps(PROVENANCE),
        parent=mine.revision_id,
    ).scalar_one()  # fmt: skip
    sql(jobs_admin, "UPDATE classes SET current_revision_id = :r WHERE id = :c",
        r=newer_id, c=mine.class_id)  # fmt: skip
    put_correction(jobs_admin, mine.owner, mine.class_id, 1, 1)
    put_correction(jobs_admin, mine.owner, mine.class_id, 2, -3, fingerprint=OTHER_FP)
    got = await mine.duplicate()  # revision 1
    assert got.status_code == 201, got.text
    copied = corrections(jobs_admin, got.json()["class_id"])
    assert [(r.fingerprint, r.song, r.beats) for r in copied] == [(FP, 1, 1)]


async def test_a_copy_without_a_music_source_or_soundless_gets_no_corrections(
    browsers: Any, jobs_admin: Connection, soundless: Callable[..., None]
) -> None:
    browser: Browser = await browsers(mowl=False)
    picked = {
        "source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST_ID,
        "origin": "song_pick", "class_playlist_id": str(uuid.uuid4()),
    }  # fmt: skip
    on_picked = Source(jobs_admin, browser, music_source=picked, music=with_beat_one())
    # A soundless class whose chosen revision still names music with a fingerprint: the class's
    # kind decides, so the copy (``{source: "none"}``) gets nothing.
    soundless_class = Source(jobs_admin, browser, music_source=NONE, music=with_beat_one())
    # A class on a playlist whose chosen revision is soundless music: no fingerprint, nothing.
    soundless_revision = Source(jobs_admin, browser, music=dict(NONE))
    soundless()
    for source in (on_picked, soundless_class, soundless_revision):
        put_correction(jobs_admin, source.owner, source.class_id, 1, 2)
        got = await source.duplicate()
        assert got.status_code == 201, got.text
        assert corrections(jobs_admin, got.json()["class_id"]) == []
        assert len(corrections(jobs_admin, source.class_id)) == 1  # the source keeps its rows


async def test_the_copy_holds_at_most_the_cap_of_one_fingerprint(
    two: tuple[Source, Source],
) -> None:
    """The source cannot hold more than 200 rows for one fingerprint through the API; planted
    ones still never give the copy more than the cap (lowest songs first)."""
    mine, _ = two
    sql(
        mine.admin,
        "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
        "SELECT :o, :c, :f, s, 1, 128 FROM generate_series(1, 201) s",
        o=mine.owner, c=mine.class_id, f=FP,
    )  # fmt: skip
    got = await mine.duplicate()
    assert got.status_code == 201, got.text
    copied = corrections(mine.admin, got.json()["class_id"])
    assert [r.song for r in copied] == list(range(1, 201))


async def test_a_refused_or_replayed_duplicate_copies_no_corrections(
    two: tuple[Source, Source],
) -> None:
    mine, _ = two
    put_correction(mine.admin, mine.owner, mine.class_id, 1, 2)
    put_correction(mine.admin, mine.owner, mine.class_id, 2, -1)
    refused = await mine.duplicate(content_hash="0" * 64)
    assert refused.status_code == 409
    mine.free_plan()
    mine.hold(3)
    over = await mine.duplicate()
    assert (over.status_code, over.json()["detail"]["quota"]) == (409, "classes")
    assert owned_corrections(mine.admin, mine.owner) == 2
    sql(mine.admin, "DELETE FROM credit_ledger WHERE owner_id = :o", o=mine.owner)
    key = new_key()
    first = await mine.duplicate(key=key)
    again = await mine.duplicate(key=key)
    assert (first.status_code, again.status_code) == (201, 200), again.text
    assert len(corrections(mine.admin, first.json()["class_id"])) == 2
    assert owned_corrections(mine.admin, mine.owner) == 4  # the source's 2 and one copy of them


async def test_copied_corrections_live_and_die_with_the_copy_and_the_account(
    two: tuple[Source, Source],
) -> None:
    mine, _ = two
    put_correction(mine.admin, mine.owner, mine.class_id, 3, -1, bpm=124.5)
    first = (await mine.duplicate()).json()
    second = (await mine.duplicate(title="Second copy")).json()
    # Deleting the source leaves the copies' rows.
    headers = await mine.browser.headers()
    deleted = await mine.browser.client.delete(f"/api/classes/{mine.class_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert corrections(mine.admin, mine.class_id) == []
    assert len(corrections(mine.admin, first["class_id"])) == 1
    # The export holds the copies' rows (guard 12: without the fingerprint).
    document = await build_export(mine.browser.env.factory, make_context(mine.owner))
    assert document is not None
    exported = document["beat_one_corrections"]
    assert sorted((r["class_id"], r["song"], r["beats"], r["bpm"]) for r in exported) == sorted(
        [(first["class_id"], 3, -1, 124.5), (second["class_id"], 3, -1, 124.5)]
    )
    assert all("fingerprint" not in r for r in exported)
    # Deleting one copy deletes its rows only (the class cascade).
    deleted = await mine.browser.client.delete(
        f"/api/classes/{first['class_id']}", headers=await mine.browser.headers()
    )
    assert deleted.status_code == 200, deleted.text
    assert corrections(mine.admin, first["class_id"]) == []
    assert len(corrections(mine.admin, second["class_id"])) == 1
    # Deleting the account deletes the rest (guard 10).
    sql(mine.admin, "DELETE FROM users WHERE id = :o", o=mine.owner)
    assert owned_corrections(mine.admin, mine.owner) == 0
