"""Reading a ``swedebeat_match`` revision through the class API (ADR 0016 section 6; issue #458).

``GET /api/classes/{id}/revisions/{revision_id}`` returns the owner's own match as a fixed shape,
and the class detail points at the class's newest match. Another user's, an unknown and a malformed
id are 404 ``not_found``; a row that no longer checks is 500 ``internal`` with nothing of it; with
the ``swedebeat_enabled`` flag off a match id is 404 ``feature_off`` and no match content is read.
Setup rows are written as the superuser; everything under test runs as ``classbeat_app`` through
the web app. No Swedebeat, MOWL or Spotify contact.
"""

import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from classbeat.classes import match_view
from classbeat.db import create_session_factory, user_session
from classbeat.swedebeat_match.revision import Matched, MatchedSong, Wanted, build, content_hash
from ctx import make_context
from jobapi import Browser
from jobtools import new_class
from test_classes_api import content_digest, insert, make_current, revision

pytestmark = pytest.mark.usefixtures("jobs_admin")

NOT_FOUND = {"detail": {"reason": "not_found"}}
FEATURE_OFF = {"detail": {"reason": "feature_off"}}
INTERNAL = {"detail": {"reason": "internal"}}

SONG_LIST: dict[str, Any] = {"v": 2, "songs": [{"title": "list song"}]}
LIST_HASH = content_digest(SONG_LIST)
PLANTED_REFERENCE = "planted-catalogue-reference-7c1"
PLANTED_URL = "https://cloud.swedebeat.se/planted/7c1.mp3"
PLANTED_TOKEN = "planted-swedebeat-token-7c1"
PLANTED_SPOTIFY = "4uLU6hMCjMI75M1A2tKUQC"
PLANTED = (PLANTED_REFERENCE, PLANTED_URL, PLANTED_TOKEN, PLANTED_SPOTIFY)
SONG_KEYS = {"position", "status", "wanted", "swedebeat"}
BODY_KEYS = {
    "id", "number", "kind", "parent_id", "content_hash",
    "songs", "found", "unsure", "missing", "swedebeat_sec",
}  # fmt: skip


def songs() -> list[MatchedSong]:
    """Found (with and without a length), unsure and missing, in play order."""
    return [
        MatchedSong(
            position=1,
            status="found",
            wanted=Wanted(title="Climb Song", artist="Ride Band"),
            swedebeat=Matched(title="Climb Song (Radio)", artist="Ride Band", duration=241),
        ),
        MatchedSong(
            position=2,
            status="unsure",
            wanted=Wanted(title="Sprint Song", artist="Fast Folk"),
            swedebeat=Matched(title="Sprint Songs", artist="Fast Folks", duration=200),
        ),
        MatchedSong(
            position=3,
            status="missing",
            wanted=Wanted(title="Rare Song", artist="Nobody Known"),
            swedebeat=None,
        ),
        MatchedSong(
            position=4,
            status="found",
            wanted=Wanted(title="Cool Down", artist="Calm Crew"),
            swedebeat=Matched(title="Cool Down", artist="Calm Crew", duration=None),
        ),
    ]


def song_list(admin: Connection, owner: UUID, class_id: UUID, number: int) -> UUID:
    return insert(
        admin,
        "class_revisions",
        owner_id=owner,
        class_id=class_id,
        number=number,
        kind="song_list",
        content=SONG_LIST,
        content_hash=LIST_HASH,
        music={},
        provenance={"origin": "design_with_songs"},
    )


def raw_match(
    admin: Connection,
    owner: UUID,
    class_id: UUID,
    parent: UUID,
    number: int,
    *,
    content: dict[str, Any],
    stored_hash: bytes,
    music: dict[str, Any],
    provenance: dict[str, Any],
) -> UUID:
    return insert(
        admin,
        "class_revisions",
        owner_id=owner,
        class_id=class_id,
        number=number,
        kind="swedebeat_match",
        content=content,
        content_hash=stored_hash,
        music=music,
        provenance=provenance,
        parent_id=parent,
    )


def made() -> tuple[dict[str, Any], bytes, dict[str, Any], dict[str, Any]]:
    """(content, hash, music, provenance) as the B1 job stores them."""
    m = build(songs(), song_list_id=uuid.uuid4(), song_list_hash=LIST_HASH)
    return (
        m.content.model_dump(mode="json"),
        m.content_hash,
        m.music.model_dump(mode="json"),
        dict(m.provenance),
    )


def match(admin: Connection, owner: UUID, class_id: UUID, parent: UUID, number: int) -> UUID:
    """A well-formed match whose provenance also carries planted values the API must drop."""
    content, digest, music, provenance = made()
    provenance |= {"reference": PLANTED_REFERENCE, "url": PLANTED_URL, "token": PLANTED_TOKEN}
    return raw_match(
        admin, owner, class_id, parent, number,
        content=content, stored_hash=digest, music=music, provenance=provenance,
    )  # fmt: skip


class Plant:
    """A user with a class: a current draft, its song list and one match of that list."""

    def __init__(self, admin: Connection, browser: Browser) -> None:
        self.admin, self.browser, self.owner = admin, browser, browser.user_id
        self.class_id = new_class(admin, self.owner)
        self.draft = revision(admin, self.owner, self.class_id, 1)
        make_current(admin, self.class_id, self.draft)
        self.list_id = song_list(admin, self.owner, self.class_id, 2)
        self.match = match(admin, self.owner, self.class_id, self.list_id, 3)

    def path(self, revision_id: object, class_id: object = None) -> str:
        return f"/api/classes/{class_id or self.class_id}/revisions/{revision_id}"

    def sql(self, statement: str, **params: Any) -> Any:
        return self.admin.execute(text(statement), params)


@pytest.fixture
def flag(env: Any) -> Iterator[Callable[[bool], None]]:
    """Switch the environment's ``swedebeat_enabled`` for the web app; restored afterwards."""
    before = env.app.state.settings

    def switch(on: bool) -> None:
        env.app.state.settings = before.model_copy(update={"swedebeat_enabled": on})

    try:
        yield switch
    finally:
        env.app.state.settings = before


@pytest.fixture
async def two(browsers: Any, jobs_admin: Connection) -> tuple[Plant, Plant]:
    a: Browser = await browsers(mowl=False)
    b: Browser = await browsers(mowl=False)
    return Plant(jobs_admin, a), Plant(jobs_admin, b)


def clean(response: Any) -> None:
    for bad in (*PLANTED, "threshold", "matcher_version", "song_list_hash", "provenance"):
        assert bad not in response.text, bad
    assert "0.85" not in response.text  # the matcher threshold's value


async def test_the_owner_reads_a_match_with_found_unsure_and_missing(
    two: tuple[Plant, Plant], flag: Callable[[bool], None]
) -> None:
    mine, _ = two
    flag(True)
    got = await mine.browser.get(mine.path(mine.match))
    assert got.status_code == 200, got.text
    body = got.json()
    assert set(body) == BODY_KEYS
    content, digest, _, _ = made()
    assert body == {
        "id": str(mine.match),
        "number": 3,
        "kind": "swedebeat_match",
        "parent_id": str(mine.list_id),
        "content_hash": digest.hex(),
        "songs": [
            {
                "position": 1,
                "status": "found",
                "wanted": {"title": "Climb Song", "artist": "Ride Band"},
                "swedebeat": {
                    "title": "Climb Song (Radio)",
                    "artist": "Ride Band",
                    "duration": 241,
                },
            },
            {
                "position": 2,
                "status": "unsure",
                "wanted": {"title": "Sprint Song", "artist": "Fast Folk"},
                "swedebeat": {"title": "Sprint Songs", "artist": "Fast Folks", "duration": 200},
            },
            {
                "position": 3,
                "status": "missing",
                "wanted": {"title": "Rare Song", "artist": "Nobody Known"},
                "swedebeat": None,
            },
            {
                "position": 4,
                "status": "found",
                "wanted": {"title": "Cool Down", "artist": "Calm Crew"},
                "swedebeat": {"title": "Cool Down", "artist": "Calm Crew", "duration": None},
            },
        ],
        "found": 2,
        "unsure": 1,
        "missing": 1,
        "swedebeat_sec": 241,  # found songs only; the unsure one is never counted
    }
    assert all(set(s) == SONG_KEYS for s in body["songs"])
    assert content_hash(content) == digest
    clean(got)

    # Drafts and song lists read as before with the flag on.
    draft = await mine.browser.get(mine.path(mine.draft))
    assert (draft.status_code, draft.json()["kind"]) == (200, "draft")


async def test_the_class_detail_points_at_the_newest_match_and_says_if_it_is_approved(
    two: tuple[Plant, Plant], flag: Callable[[bool], None]
) -> None:
    mine, theirs = two
    flag(True)
    detail = f"/api/classes/{mine.class_id}"
    shown = await mine.browser.get(detail)
    assert shown.status_code == 200, shown.text
    assert shown.json()["swedebeat_match"] == {"revision_id": str(mine.match), "approved": False}

    newer = match(mine.admin, mine.owner, mine.class_id, mine.list_id, 4)
    mine.sql(
        "UPDATE classes SET approved_swedebeat_match_id = :m WHERE id = :c",
        m=mine.match,
        c=mine.class_id,
    )
    shown = (await mine.browser.get(detail)).json()
    assert shown["swedebeat_match"] == {"revision_id": str(newer), "approved": False}
    mine.sql(
        "UPDATE classes SET approved_swedebeat_match_id = :m WHERE id = :c",
        m=newer,
        c=mine.class_id,
    )
    shown = await mine.browser.get(detail)
    assert shown.json()["swedebeat_match"] == {"revision_id": str(newer), "approved": True}
    clean(shown)
    for song in songs():  # the detail carries no title or artist of the match
        assert song.wanted.title not in shown.text
    # The class list gains nothing.
    listed = await mine.browser.get("/api/classes")
    assert "swedebeat" not in listed.text

    # A class without a match has a null pointer; theirs never points at mine.
    bare = new_class(mine.admin, mine.owner, "Bare")
    assert (await mine.browser.get(f"/api/classes/{bare}")).json()["swedebeat_match"] is None
    their = (await theirs.browser.get(f"/api/classes/{theirs.class_id}")).json()
    assert their["swedebeat_match"]["revision_id"] == str(theirs.match)


async def test_another_users_unknown_and_malformed_ids_are_the_same_404(
    two: tuple[Plant, Plant], flag: Callable[[bool], None]
) -> None:
    mine, theirs = two
    flag(True)
    own_other = new_class(mine.admin, mine.owner, "Other")
    other_list = song_list(mine.admin, mine.owner, own_other, 1)
    other_match = match(mine.admin, mine.owner, own_other, other_list, 2)
    unknown = uuid.uuid4()
    cases = [
        mine.path(theirs.match, theirs.class_id),  # their class, their match
        mine.path(theirs.match),  # my class, their match
        mine.path(other_match),  # my class, my match of another class
        mine.path(unknown),
        mine.path(unknown, unknown),
        mine.path("nope"),
        mine.path(mine.match, "nope"),
    ]
    for path in cases:
        got = await mine.browser.get(path)
        assert (got.status_code, got.json()) == (404, NOT_FOUND), path
        for song in songs():
            assert song.wanted.title not in got.text
    detail = await mine.browser.get(f"/api/classes/{theirs.class_id}")
    assert (detail.status_code, detail.json()) == (404, NOT_FOUND)
    # The owner still reads it.
    assert (await theirs.browser.get(theirs.path(theirs.match))).status_code == 200


def _extra_top(content: dict[str, Any]) -> dict[str, Any]:
    return {**content, "reference": PLANTED_REFERENCE}


def _extra_song(content: dict[str, Any]) -> dict[str, Any]:
    first = {**content["songs"][0], "spotify_id": PLANTED_SPOTIFY}
    return {**content, "songs": [first, *content["songs"][1:]]}


def _extra_hit(content: dict[str, Any]) -> dict[str, Any]:
    first = dict(content["songs"][0])
    first["swedebeat"] = {**first["swedebeat"], "url": PLANTED_URL}
    return {**content, "songs": [first, *content["songs"][1:]]}


@pytest.mark.parametrize(
    "case",
    [
        "extra_top_rehashed",
        "extra_song_rehashed",
        "extra_hit_rehashed",
        "changed_title_old_hash",
        "wrong_hash",
        "wrong_totals_rehashed",
        "music_extra_field",
        "music_other_list",
    ],
)
async def test_a_row_that_no_longer_checks_is_a_500_with_nothing_of_it(
    two: tuple[Plant, Plant], flag: Callable[[bool], None], case: str
) -> None:
    mine, _ = two
    flag(True)
    content, digest, music, provenance = made()
    if case == "extra_top_rehashed":
        content = _extra_top(content)
        digest = content_hash(content)
    elif case == "extra_song_rehashed":
        content = _extra_song(content)
        digest = content_hash(content)
    elif case == "extra_hit_rehashed":
        content = _extra_hit(content)
        digest = content_hash(content)
    elif case == "changed_title_old_hash":
        content["songs"][0]["wanted"]["title"] = PLANTED_TOKEN
    elif case == "wrong_hash":
        digest = bytes.fromhex("ab" * 32)
    elif case == "wrong_totals_rehashed":
        content["found"] = 4
        digest = content_hash(content)
    elif case == "music_extra_field":
        music = {**music, "token": PLANTED_TOKEN}
    else:
        music = {**music, "song_list_hash": "cd" * 32}
    bad = raw_match(
        mine.admin, mine.owner, mine.class_id, mine.list_id, 4,
        content=content, stored_hash=digest, music=music, provenance=provenance,
    )  # fmt: skip
    got = await mine.browser.get(mine.path(bad))
    assert (got.status_code, got.json()) == (500, INTERNAL)
    for shown in (*PLANTED, "Climb Song", "Ride Band", digest.hex()):
        assert shown not in got.text
    # The good match beside it still reads.
    assert (await mine.browser.get(mine.path(mine.match))).status_code == 200


async def test_flag_off_answers_feature_off_and_reads_no_match(
    two: tuple[Plant, Plant], flag: Callable[[bool], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    mine, theirs = two
    reads: list[str] = []

    async def no_get(*_: Any, **__: Any) -> Any:
        reads.append("get_match")
        raise AssertionError("a match was read with the flag off")

    async def no_newest(*_: Any, **__: Any) -> Any:
        reads.append("newest_match")
        raise AssertionError("a match pointer was read with the flag off")

    monkeypatch.setattr(match_view, "get_match", no_get)
    monkeypatch.setattr(match_view, "newest_match", no_newest)

    def stored_flag(value: str | None) -> None:
        mine.sql("DELETE FROM app_settings WHERE key = 'swedebeat_enabled'")
        if value is not None:
            mine.sql(
                "INSERT INTO app_settings (key, value) "
                "VALUES ('swedebeat_enabled', CAST(:v AS jsonb))",
                v=json.dumps(value),
            )

    try:
        for env_on, stored in ((False, None), (False, "on"), (True, "off")):
            flag(env_on)
            stored_flag(stored)
            got = await mine.browser.get(mine.path(mine.match))
            assert (got.status_code, got.json()) == (404, FEATURE_OFF), (env_on, stored)
            for path in (mine.path(uuid.uuid4()), mine.path(theirs.match), mine.path("nope")):
                other = await mine.browser.get(path)
                assert (other.status_code, other.json()) == (404, NOT_FOUND), path
            detail = await mine.browser.get(f"/api/classes/{mine.class_id}")
            assert detail.status_code == 200, detail.text
            assert "swedebeat_match" not in detail.json()
            # Drafts and song lists are unchanged.
            draft = await mine.browser.get(mine.path(mine.draft))
            assert (draft.status_code, draft.json()["kind"]) == (200, "draft")
        assert reads == []
    finally:
        stored_flag(None)


@pytest.fixture
async def around_rls(database_url: str) -> AsyncIterator[Any]:
    """A session factory of the superuser: row-level security does not apply to it."""
    engine = create_async_engine(database_url)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


async def test_the_reads_filter_on_the_owner_themselves_with_rls_out_of_the_way(
    two: tuple[Plant, Plant], around_rls: Any
) -> None:
    mine, theirs = two
    ctx = make_context(mine.owner)
    async with user_session(around_rls, ctx) as db:
        # Self-check: with this factory both users' matches are visible.
        seen = await db.execute(
            text("SELECT count(*) FROM class_revisions WHERE kind = 'swedebeat_match'")
        )
        assert seen.scalar_one() == 2
        assert await match_view.newest_match(db, mine.owner, theirs.class_id) is None
        own = await match_view.newest_match(db, mine.owner, mine.class_id)
        assert own is not None
        assert own.revision_id == mine.match
    assert await match_view.get_match(around_rls, ctx, theirs.class_id, theirs.match) is None
    assert await match_view.is_match(around_rls, ctx, theirs.class_id, theirs.match) is False
    assert await match_view.get_match(around_rls, ctx, mine.class_id, theirs.match) is None
    assert await match_view.is_match(around_rls, ctx, mine.class_id, theirs.match) is False
    found = await match_view.get_match(around_rls, ctx, mine.class_id, mine.match)
    assert isinstance(found, match_view.MatchView)
    assert found.id == mine.match
    assert await match_view.is_match(around_rls, ctx, mine.class_id, mine.match) is True
