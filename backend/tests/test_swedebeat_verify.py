"""B2 of ADR 0016: the Swedebeat hand-off, on the fakes only.

A new mix appears in the fake MOWL, is analysed, and the verdict is ok, too_short, too_long or
not_found. The buttons are idempotent. Deleting the class or disconnecting MOWL drops the row.
A stored match counts only when it is the current draft's song list.
"""

import asyncio
import json
import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest

from agentfake import CONFIG, FactoryModels, from_dict
from authtools import running_env
from classbeat import design, swedebeat_handoff
from classbeat.classes import handoff as handoff_api
from classbeat.connections.repository import disconnect
from classbeat.design import mixfetch
from classbeat.design.swedebeat_music import load_swedebeat_mix, swedebeat_step
from classbeat.egress import Allowlist, mowl_media_test_client
from classbeat.jobs import JobFailure, KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.songs.song_list import Song, SongListContent, SongListMusic, content_hash
from classbeat.songs.song_list import fingerprint as song_fingerprint
from classbeat.swedebeat_handoff import kind as verify_kind
from classbeat.swedebeat_handoff.config import HandoffConfig
from classbeat.swedebeat_handoff.kind import KIND, VERIFY_MIX
from classbeat.swedebeat_handoff.verdict import judge
from classbeat.swedebeat_match.revision import Matched, MatchedSong, Wanted, build
from ctx import make_context
from designtools import BRIEF
from jobapi import Browser, new_key
from mediafake import FakeMedia
from mowlfake import FakeMowl
from optools import TOKEN_A, OpsEnv
from preftools import use_swedebeat

pytestmark = pytest.mark.usefixtures("jenv", "mowl_api")


def _song(seconds: int, n: int = 1) -> Song:
    return Song(
        spotify_id=f"{n:022d}"[-22:],
        title=f"Song {n}",
        artist="Band",
        role="climb",
        bpm=90.0,
        length_sec=seconds,
        new=False,
        cover=None,
    )


def _matched(seconds: int, n: int = 1) -> MatchedSong:
    return MatchedSong(
        position=n,
        status="found",
        wanted=Wanted(title=f"Song {n}", artist="Band"),
        swedebeat=Matched(title=f"Song {n}", artist="Band", duration=seconds),
    )


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> Any:
    async with running_env(app_database_url, swedebeat_enabled=True) as running:
        yield running


@pytest.fixture
def work(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    root = tmp_path / "audio"
    root.mkdir()
    monkeypatch.setattr(mixfetch, "work_root", lambda: root)
    return root


class HandEnv:
    def __init__(self, ops: OpsEnv, media: FakeMedia, monkeypatch: pytest.MonkeyPatch) -> None:
        self.ops = ops
        self.media = media
        injected = Allowlist(frozenset({FakeMedia.HOST}))
        client = lambda: mowl_media_test_client(injected, media)  # noqa: E731
        for fn in (load_swedebeat_mix, swedebeat_step):
            defaults = dict(fn.__kwdefaults__ or {})
            defaults["client_factory"] = client
            defaults["index_poll_seconds"] = 0.01
            monkeypatch.setattr(fn, "__kwdefaults__", defaults)
        swedebeat_handoff.configure(HandoffConfig(enabled=True, keyring=ops.keyring))

    async def run_verify(self) -> UUID:
        registry = KindRegistry()
        registry.register(VERIFY_MIX)
        lease = self.ops.app.settings.job_lease_seconds
        row = await queue.claim(self.ops.factory, "b2-w", lease, [KIND])
        assert row is not None, "nothing to claim"
        await execute(
            Execution(row),
            self.ops.factory,
            registry,
            RunConfig(lease_seconds=lease),
            asyncio.Event(),
        )
        return row.job_id

    def sql(self, statement: str, **params: Any) -> Any:
        return self.ops.sql(statement, **params)


@pytest.fixture
def henv(xenv: OpsEnv, mowl_api: FakeMowl, monkeypatch: pytest.MonkeyPatch) -> Iterator[HandEnv]:
    made = HandEnv(xenv, mowl_api.media, monkeypatch)
    try:
        yield made
    finally:
        swedebeat_handoff.configure(None)
        design.configure(None)


async def rider(browsers: Any, henv: HandEnv) -> Browser:
    browser: Browser = await browsers()
    await use_swedebeat(henv.ops.factory, browser.user_id)  # #525: the user's own switch
    henv.ops.app.app.dependency_overrides.clear()
    browser.connection_id = await henv.ops.connect(browser.user_id, TOKEN_A)
    return browser


def plant(henv: HandEnv, owner: UUID, lengths: list[int]) -> UUID:
    """A class whose current draft's song list is matched and approved."""
    songs = [_song(seconds, n) for n, seconds in enumerate(lengths, start=1)]
    content = SongListContent(
        v=2,
        mode="pick",
        target_sec=sum(lengths),
        total_sec=sum(lengths),
        crossfade_sec=0.5,
        songs=songs,
        omitted_no_bpm=0,
        note="",
    )
    dumped = content.model_dump(mode="json")
    digest = content_hash(dumped)
    music = SongListMusic(
        source="claude_pick",
        fingerprint=song_fingerprint(tuple(song.spotify_id for song in songs)),
        spotify_user_id="spotify-user-test",
    ).model_dump(mode="json")
    matched = [_matched(seconds, n) for n, seconds in enumerate(lengths, start=1)]
    match = build(matched, song_list_id=uuid.uuid4(), song_list_hash=digest)
    class_id, list_id, draft_id, match_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # build() captured a placeholder parent; the stored parent is the list.
    henv.sql(
        "INSERT INTO classes (id, owner_id, title, brief) VALUES (:id, :o, 'Ride', "
        "CAST(:brief AS jsonb))",
        id=class_id,
        o=owner,
        brief=json.dumps(BRIEF),
    )
    henv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance) VALUES (:id, :o, :c, 1, 'song_list', "
        "CAST(:content AS jsonb), :hash, CAST(:music AS jsonb), '{}')",
        id=list_id,
        o=owner,
        c=class_id,
        content=json.dumps(dumped),
        hash=digest,
        music=json.dumps(music),
    )
    draft_music = {
        "source": "mowl_spotify_playlist",
        "link_id": str(uuid.uuid4()),
        "fingerprint": "ab" * 32,
        "unknown_bpm": [],
        "song_list_id": str(list_id),
    }
    henv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance) VALUES (:id, :o, :c, 2, 'draft', "
        "CAST(:content AS jsonb), :hash, CAST(:music AS jsonb), '{}')",
        id=draft_id,
        o=owner,
        c=class_id,
        content="{}",
        hash=b"\x11" * 32,
        music=json.dumps(draft_music),
    )
    stored = match.content.model_dump(mode="json")
    # The match's parent is the list, and its hash is the list's.
    rebuilt = build(matched, song_list_id=list_id, song_list_hash=digest)
    henv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance, parent_id) VALUES (:id, :o, :c, 3, "
        "'swedebeat_match', CAST(:content AS jsonb), :hash, CAST(:music AS jsonb), "
        "CAST(:prov AS jsonb), :parent)",
        id=match_id,
        o=owner,
        c=class_id,
        content=json.dumps(rebuilt.content.model_dump(mode="json")),
        hash=rebuilt.content_hash,
        music=json.dumps(rebuilt.music.model_dump(mode="json")),
        prov=json.dumps(rebuilt.provenance),
        parent=list_id,
    )
    henv.sql(
        "UPDATE classes SET current_revision_id = :draft, approved_swedebeat_match_id = :match, "
        "music_source = CAST(:source AS jsonb) WHERE id = :id",
        draft=draft_id,
        match=match_id,
        id=class_id,
        source=json.dumps({"source": "mowl_spotify_playlist", "origin": "song_pick"}),
    )
    assert stored["swedebeat_sec"] == sum(lengths)
    return class_id


def _draft_script() -> dict[str, Any]:
    seated = {"standing": False}
    draft = {
        "name": "Invented mix ride",
        "segments": [
            {
                "name": "Warm-up",
                "kind": "warmup",
                "intervals": [{"sec": 45, "zone": 2, "rpm": "beat", **seated}],
            },
            {
                "name": "Push",
                "kind": "main",
                "intervals": [{"sec": 45, "zone": 4, "rpm": [80, 95], **seated}],
            },
            {
                "name": "Cool down",
                "kind": "cooldown",
                "intervals": [{"sec": 60, "zone": 1, "rpm": [70, 85], **seated}],
            },
        ],
    }
    body = {"draft": {"$draft": "good"}}
    return {
        "drafts": {"good": draft},
        "turns": [
            {
                "calls": [
                    {"name": "get_music", "input": {}},
                    {"name": "get_music", "input": {"track": 0}},
                    {"name": "preview_draft", "input": body},
                ]
            },
            {"calls": [{"name": "submit_draft", "input": body}]},
        ],
    }


# --- the length band, without a database ----------------------------------------------------------


def test_the_length_band_names_ok_too_short_and_too_long() -> None:
    one = [_matched(30)]
    assert judge(one, 30).verdict == "ok"
    assert judge(one, 80).verdict == "too_long"
    three = [_matched(40, 1), _matched(40, 2), _matched(40, 3)]
    short = judge(three, 30)
    assert short.verdict == "too_short"
    assert short.missing_positions == (2, 3)


# --- on the fake ----------------------------------------------------------------------------------


async def _start(browser: Browser, class_id: UUID) -> Any:
    return await browser.post(f"/api/classes/{class_id}/swedebeat-handoff", {})


async def _check(browser: Browser, class_id: UUID, mix_id: str | None = None) -> Any:
    body = {} if mix_id is None else {"mix_id": mix_id}
    return await browser.post(
        f"/api/classes/{class_id}/swedebeat-handoff/check", body, {"Idempotency-Key": new_key()}
    )


async def test_a_new_mix_is_analysed_ok_and_designs_the_class(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, henv)
    old = mowl_api.add_mix("Already there", token=TOKEN_A, mix_id="old-mix", listed_seconds=150)
    class_id = plant(henv, browser.user_id, [150])
    started = await _start(browser, class_id)
    assert started.status_code == 200, started.text
    assert started.json()["state"] == "open"
    calls = mowl_api.list_calls
    again = await _start(browser, class_id)
    assert again.status_code == 200, again.text
    assert mowl_api.list_calls == calls  # the live row is not snapshotted again
    new = mowl_api.add_mix(
        "The new mix",
        token=TOKEN_A,
        mix_id="new-mix",
        listed_seconds=150,
        audio_seconds=150,
        link="beats",
    )
    checked = await _check(browser, class_id)
    assert checked.status_code == 201, checked.text
    busy = await _check(browser, class_id)
    assert busy.status_code == 409
    assert busy.json()["detail"]["reason"] == "class_busy"
    job_id = await henv.run_verify()
    assert (henv.ops.row(job_id).state, henv.ops.row(job_id).error_category) == ("completed", None)
    view = await browser.get(f"/api/classes/{class_id}/swedebeat-handoff")
    assert view.status_code == 200, view.text
    assert view.json()["verdict"] == "ok"
    assert view.json()["mix_id"] == new
    assert mowl_api.attaches == [new]
    quiet = await _check(browser, class_id)
    assert quiet.status_code == 200
    assert quiet.json()["job_id"] is None
    assert (
        henv.sql(
            "SELECT count(*) FROM jobs WHERE owner_id = :o AND kind = :k", o=browser.user_id, k=KIND
        ).scalar_one()
        == 1
    )
    # The hand-off into A4: the class is designed on the mix, not the Spotify playlist.
    design.configure(
        design.Runtime(
            keyring=henv.ops.keyring,
            models=FactoryModels(lambda: from_dict(_draft_script())),
            agent=CONFIG,
            swedebeat_enabled=True,
        )
    )
    # #525: with "I use Swedebeat" off the design admission refuses; no job, no credit.
    await use_swedebeat(henv.ops.factory, browser.user_id, on=False)
    refused = await browser.post(
        f"/api/classes/{class_id}/swedebeat-handoff/design", {}, {"Idempotency-Key": new_key()}
    )
    assert (refused.status_code, refused.json()) == (404, {"detail": {"reason": "feature_off"}})
    assert (
        henv.sql(
            "SELECT count(*) FROM jobs WHERE owner_id = :o AND kind = :k",
            o=browser.user_id,
            k=design.KIND,
        ).scalar_one()
        == 0
    )
    await use_swedebeat(henv.ops.factory, browser.user_id)
    designed = await browser.post(
        f"/api/classes/{class_id}/swedebeat-handoff/design", {}, {"Idempotency-Key": new_key()}
    )
    assert designed.status_code == 201, designed.text
    design_id = UUID(designed.json()["job_id"])
    held = henv.sql("SELECT credit_state FROM jobs WHERE id = :id", id=design_id).scalar_one()
    assert held == "reserved"
    registry = KindRegistry()
    registry.register(design.DESIGN_DRAFT)
    lease = henv.ops.app.settings.job_lease_seconds
    row = await queue.claim(henv.ops.factory, "b2-d", lease, [design.KIND])
    assert row is not None
    await execute(
        Execution(row), henv.ops.factory, registry, RunConfig(lease_seconds=lease), asyncio.Event()
    )
    finished = henv.ops.row(row.job_id)
    assert finished.state == "completed", finished.error_category
    assert finished.credit_state == "reserved"  # settle is the worker's; the row was reserved
    source = henv.sql("SELECT music_source FROM classes WHERE id = :id", id=class_id).scalar_one()
    assert source["source"] == "mowl_swedebeat_mix"
    assert source["mix_id"] == new
    assert old == "old-mix"


async def test_too_short_too_long_not_found_and_the_override(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, henv)
    # not_found: the snapshot already holds every mix.
    only = mowl_api.add_mix("Only", token=TOKEN_A, mix_id="only-mix", listed_seconds=30)
    missing = plant(henv, browser.user_id, [30])
    assert (await _start(browser, missing)).status_code == 200
    assert (await _check(browser, missing)).status_code == 201
    await henv.run_verify()
    body = (await browser.get(f"/api/classes/{missing}/swedebeat-handoff")).json()
    assert body["verdict"] == "not_found"
    assert mowl_api.attaches == []
    # too_short, then the override verifies without another attach.
    short_id = plant(henv, browser.user_id, [40, 40, 40])
    assert (await _start(browser, short_id)).status_code == 200
    mowl_api.add_mix(
        "Short",
        token=TOKEN_A,
        mix_id="short-mix",
        listed_seconds=30,
        audio_seconds=30,
        link="beats",
    )
    assert (await _check(browser, short_id)).status_code == 201
    await henv.run_verify()
    short = (await browser.get(f"/api/classes/{short_id}/swedebeat-handoff")).json()
    assert short["verdict"] == "too_short"
    assert short["missing_positions"]
    attaches = list(mowl_api.attaches)
    overridden = await browser.post(
        f"/api/classes/{short_id}/swedebeat-handoff/override", {"mix_id": "short-mix"}
    )
    assert overridden.status_code == 200, overridden.text
    assert overridden.json()["state"] == "verified"
    assert mowl_api.attaches == attaches
    # too_long.
    long_id = plant(henv, browser.user_id, [30])
    assert (await _start(browser, long_id)).status_code == 200
    mowl_api.add_mix(
        "Long", token=TOKEN_A, mix_id="long-mix", listed_seconds=80, audio_seconds=80, link="beats"
    )
    assert (await _check(browser, long_id)).status_code == 201
    await henv.run_verify()
    long = (await browser.get(f"/api/classes/{long_id}/swedebeat-handoff")).json()
    assert long["verdict"] == "too_long"
    assert long["mix_id"] == "long-mix"
    assert only == "only-mix"


async def test_a_stored_match_of_another_song_list_is_not_approved(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, henv)
    mowl_api.add_mix("Old", token=TOKEN_A, mix_id="before", listed_seconds=30)
    class_id = plant(henv, browser.user_id, [30])
    # A second draft whose song list is a different revision. Picking it clears the stored
    # match. Putting that same match back (same owner) still does not approve the new list.
    match_id = henv.sql(
        "SELECT approved_swedebeat_match_id FROM classes WHERE id = :c", c=class_id
    ).scalar_one()
    other_list = uuid.uuid4()
    other_draft = uuid.uuid4()
    songs = [_song(30)]
    content = SongListContent(
        v=2,
        mode="pick",
        target_sec=30,
        total_sec=30,
        crossfade_sec=0.5,
        songs=songs,
        omitted_no_bpm=0,
        note="other",
    )
    dumped = content.model_dump(mode="json")
    henv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance) VALUES (:id, :o, :c, 4, 'song_list', "
        "CAST(:content AS jsonb), :hash, '{}', '{}')",
        id=other_list,
        o=browser.user_id,
        c=class_id,
        content=json.dumps(dumped),
        hash=content_hash(dumped),
    )
    henv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance) VALUES (:id, :o, :c, 5, 'draft', '{}', :hash, "
        "CAST(:music AS jsonb), '{}')",
        id=other_draft,
        o=browser.user_id,
        c=class_id,
        hash=b"\x22" * 32,
        music=json.dumps(
            {
                "source": "mowl_spotify_playlist",
                "link_id": str(uuid.uuid4()),
                "fingerprint": "cd" * 32,
                "unknown_bpm": [],
                "song_list_id": str(other_list),
            }
        ),
    )
    picked = await browser.post(
        f"/api/classes/{class_id}/current",
        {"revision_id": str(other_draft), "content_hash": (b"\x22" * 32).hex()},
    )
    assert picked.status_code == 200, picked.text
    assert (
        henv.sql(
            "SELECT approved_swedebeat_match_id FROM classes WHERE id = :c", c=class_id
        ).scalar_one()
        is None
    )
    calls = mowl_api.list_calls
    refused = await _start(browser, class_id)
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "stale_match"
    assert mowl_api.list_calls == calls
    # Same owner, and the foreign key still accepts this match of this class.
    henv.sql(
        "UPDATE classes SET approved_swedebeat_match_id = :match WHERE id = :c",
        match=match_id,
        c=class_id,
    )
    again = await _start(browser, class_id)
    assert again.status_code == 409
    assert again.json()["detail"]["reason"] == "stale_match"
    assert mowl_api.list_calls == calls
    assert (
        henv.sql(
            "SELECT count(*) FROM swedebeat_handoffs WHERE class_id = :c", c=class_id
        ).scalar_one()
        == 0
    )


async def test_expired_rows_are_refused_and_deletion_drops_them(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, henv)
    mowl_api.add_mix("Kept", token=TOKEN_A, mix_id="kept", listed_seconds=30)
    class_id = plant(henv, browser.user_id, [30])
    assert (await _start(browser, class_id)).status_code == 200
    henv.sql(
        "UPDATE swedebeat_handoffs SET created_at = now() - interval '2 days', "
        "expires_at = now() - interval '1 day' WHERE class_id = :c",
        c=class_id,
    )
    calls = mowl_api.list_calls
    expired = await _check(browser, class_id)
    assert expired.status_code == 410
    assert expired.json()["detail"]["reason"] == "handoff_expired"
    assert mowl_api.attaches == []
    assert mowl_api.list_calls == calls
    # A new press of the step snapshots again; it does not revive the expired row.
    renewed = await _start(browser, class_id)
    assert renewed.status_code == 200
    assert renewed.json()["state"] == "open"
    assert (
        henv.sql(
            "SELECT count(*) FROM swedebeat_handoffs WHERE class_id = :c AND superseded_at IS NULL",
            c=class_id,
        ).scalar_one()
        == 1
    )
    deleted = await browser.client.delete(
        f"/api/classes/{class_id}", headers=await browser.headers()
    )
    # browser.post clears cookies; delete goes through the client directly.
    assert deleted.status_code == 200, deleted.text
    assert (
        henv.sql(
            "SELECT count(*) FROM swedebeat_handoffs WHERE owner_id = :o", o=browser.user_id
        ).scalar_one()
        == 0
    )


async def test_disconnecting_mowl_deletes_the_handoff(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, henv)
    mowl_api.add_mix("Mine", token=TOKEN_A, mix_id="mine", listed_seconds=30)
    class_id = plant(henv, browser.user_id, [30])
    assert (await _start(browser, class_id)).status_code == 200
    assert await disconnect(henv.ops.factory, make_context(browser.user_id), "mowl")
    assert (
        henv.sql(
            "SELECT count(*) FROM swedebeat_handoffs WHERE owner_id = :o", o=browser.user_id
        ).scalar_one()
        == 0
    )


async def test_flag_off_and_a_foreign_class_and_a_forged_body(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, henv)
    other: Browser = await browsers()
    mowl_api.add_mix("Shown", token=TOKEN_A, mix_id="shown", listed_seconds=30)
    class_id = plant(henv, browser.user_id, [30])
    henv.sql("DELETE FROM app_settings WHERE key = 'swedebeat_enabled'")
    henv.sql(
        "INSERT INTO app_settings (key, value) VALUES ('swedebeat_enabled', CAST(:v AS jsonb))",
        v=json.dumps("off"),
    )
    calls = mowl_api.list_calls
    off = await _start(browser, class_id)
    assert off.status_code == 404
    assert off.json()["detail"]["reason"] == "feature_off"
    assert mowl_api.list_calls == calls
    assert henv.sql("SELECT count(*) FROM swedebeat_handoffs").scalar_one() == 0
    henv.sql("DELETE FROM app_settings WHERE key = 'swedebeat_enabled'")
    forged = await browser.post(
        f"/api/classes/{class_id}/swedebeat-handoff/check",
        {"mix_id": "shown", "state": "verified"},
        {"Idempotency-Key": new_key()},
    )
    assert forged.status_code == 422
    foreign = await other.get(f"/api/classes/{class_id}/swedebeat-handoff")
    assert foreign.status_code == 404


async def test_picking_a_draft_of_the_same_song_list_keeps_the_match(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, henv)
    mowl_api.add_mix("Kept", token=TOKEN_A, mix_id="kept-list", listed_seconds=30)
    class_id = plant(henv, browser.user_id, [30])
    match_id = henv.sql(
        "SELECT approved_swedebeat_match_id FROM classes WHERE id = :c", c=class_id
    ).scalar_one()
    draft_id = uuid.uuid4()
    digest = b"\x44" * 32
    henv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance) "
        "SELECT :id, owner_id, class_id, 6, 'draft', '{}', :hash, music, provenance "
        "FROM class_revisions WHERE class_id = :c AND kind = 'draft'",
        id=draft_id,
        c=class_id,
        hash=digest,
    )
    picked = await browser.post(
        f"/api/classes/{class_id}/current",
        {"revision_id": str(draft_id), "content_hash": digest.hex()},
    )
    assert picked.status_code == 200, picked.text
    assert (
        henv.sql(
            "SELECT approved_swedebeat_match_id FROM classes WHERE id = :c", c=class_id
        ).scalar_one()
        == match_id
    )
    started = await _start(browser, class_id)
    assert started.status_code == 200, started.text


async def test_a_client_mix_id_absent_from_the_listing_is_refused(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, henv)
    mowl_api.add_mix("Listed", token=TOKEN_A, mix_id="listed-mix", listed_seconds=30)
    class_id = plant(henv, browser.user_id, [30])
    assert (await _start(browser, class_id)).status_code == 200
    lists = mowl_api.list_calls
    attaches = list(mowl_api.attaches)
    media = list(mowl_api.media.requests)
    checked = await _check(browser, class_id, "not-in-the-listing")
    assert checked.status_code == 201, checked.text
    job_id = await henv.run_verify()
    row = henv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert mowl_api.list_calls == lists + 1  # the job's own listing, and no second one
    assert mowl_api.attaches == attaches
    assert mowl_api.media.requests == media
    assert (
        henv.sql(
            "SELECT count(*) FROM swedebeat_analyses WHERE owner_id = :o", o=browser.user_id
        ).scalar_one()
        == 0
    )
    left = henv.sql(
        "SELECT state, mix_id FROM swedebeat_handoffs WHERE class_id = :c "
        "AND superseded_at IS NULL",
        c=class_id,
    ).one()
    assert left.state == "open"
    assert left.mix_id is None


@pytest.mark.parametrize("off", ["database", "worker"])
async def test_a_queued_verify_ends_feature_off_before_the_connection_opens(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl, monkeypatch: pytest.MonkeyPatch, off: str
) -> None:
    browser = await rider(browsers, henv)
    mowl_api.add_mix("Waiting", token=TOKEN_A, mix_id="waiting-mix", listed_seconds=30)
    class_id = plant(henv, browser.user_id, [30])
    assert (await _start(browser, class_id)).status_code == 200
    mowl_api.add_mix(
        "New",
        token=TOKEN_A,
        mix_id="new-while-queued",
        listed_seconds=30,
        audio_seconds=30,
        link="beats",
    )
    checked = await _check(browser, class_id)
    assert checked.status_code == 201, checked.text
    lists = mowl_api.list_calls
    attaches = list(mowl_api.attaches)
    media = list(mowl_api.media.requests)
    opened = 0

    async def _open(*_args: object, **_kwargs: object) -> None:
        nonlocal opened
        opened += 1
        raise JobFailure("internal")

    monkeypatch.setattr(verify_kind, "open_connection", _open)
    if off == "database":
        henv.sql("DELETE FROM app_settings WHERE key = 'swedebeat_enabled'")
        henv.sql(
            "INSERT INTO app_settings (key, value) VALUES ('swedebeat_enabled', CAST(:v AS jsonb))",
            v=json.dumps("off"),
        )
    else:
        swedebeat_handoff.configure(HandoffConfig(enabled=False, keyring=henv.ops.keyring))
    try:
        job_id = await henv.run_verify()
    finally:
        henv.sql("DELETE FROM app_settings WHERE key = 'swedebeat_enabled'")
        swedebeat_handoff.configure(HandoffConfig(enabled=True, keyring=henv.ops.keyring))
    row = henv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "feature_off")
    assert opened == 0
    assert mowl_api.list_calls == lists
    assert mowl_api.attaches == attaches
    assert mowl_api.media.requests == media


async def test_override_repeats_the_stored_approval_before_verify(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl, monkeypatch: pytest.MonkeyPatch
) -> None:
    browser = await rider(browsers, henv)
    mowl_api.add_mix("Already", token=TOKEN_A, mix_id="already-mix", listed_seconds=30)
    class_id = plant(henv, browser.user_id, [40, 40, 40])
    assert (await _start(browser, class_id)).status_code == 200
    mowl_api.add_mix(
        "Short",
        token=TOKEN_A,
        mix_id="race-mix",
        listed_seconds=30,
        audio_seconds=30,
        link="beats",
    )
    assert (await _check(browser, class_id)).status_code == 201
    await henv.run_verify()
    view = (await browser.get(f"/api/classes/{class_id}/swedebeat-handoff")).json()
    assert view["verdict"] == "too_short"
    other_list, other_draft = uuid.uuid4(), uuid.uuid4()
    songs = [_song(30)]
    content = SongListContent(
        v=2,
        mode="pick",
        target_sec=30,
        total_sec=30,
        crossfade_sec=0.5,
        songs=songs,
        omitted_no_bpm=0,
        note="other",
    )
    dumped = content.model_dump(mode="json")
    henv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance) VALUES (:id, :o, :c, 4, 'song_list', "
        "CAST(:content AS jsonb), :hash, '{}', '{}')",
        id=other_list,
        o=browser.user_id,
        c=class_id,
        content=json.dumps(dumped),
        hash=content_hash(dumped),
    )
    henv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance) VALUES (:id, :o, :c, 5, 'draft', '{}', :hash, "
        "CAST(:music AS jsonb), '{}')",
        id=other_draft,
        o=browser.user_id,
        c=class_id,
        hash=b"\x55" * 32,
        music=json.dumps(
            {
                "source": "mowl_spotify_playlist",
                "link_id": str(uuid.uuid4()),
                "fingerprint": "ee" * 32,
                "unknown_bpm": [],
                "song_list_id": str(other_list),
            }
        ),
    )

    async def _switch(*_args: object, **_kwargs: object) -> tuple[list[str], None]:
        # The listing is between the two transactions. The column stays set.
        henv.sql(
            "UPDATE classes SET current_revision_id = :draft WHERE id = :id",
            draft=other_draft,
            id=class_id,
        )
        return ["race-mix"], None

    monkeypatch.setattr(handoff_api, "list_mix_ids", _switch)
    overridden = await browser.post(
        f"/api/classes/{class_id}/swedebeat-handoff/override", {"mix_id": "race-mix"}
    )
    assert overridden.status_code == 409, overridden.text
    assert overridden.json()["detail"]["reason"] == "stale_match"
    assert (
        henv.sql(
            "SELECT approved_swedebeat_match_id FROM classes WHERE id = :c", c=class_id
        ).scalar_one()
        is not None
    )
    assert (
        henv.sql(
            "SELECT state FROM swedebeat_handoffs WHERE class_id = :c AND superseded_at IS NULL",
            c=class_id,
        ).scalar_one()
        == "checked"
    )
