"""``design_with_songs`` phase 2 (ADR 0008 sections 2 and 4; issue SJ2): from one Approve to the
final private playlist, verified, analysed in MOWL, and the class's first design. Against the
fake Spotify, the fake MOWL and ``FakeProvider``; no network, no real account.

The 120-track page rule of A3 is covered where it lives, the fill itself
(``tests/test_playlists_writes.py``): a song list holds at most ``MAX_SONGS`` (40) songs.
"""

import asyncio
import json
import uuid
from typing import Any
from uuid import UUID

import pytest

from agentfake import FactoryModels
from classbeat import connections
from classbeat.design import kind as design
from classbeat.jobs import settle
from classbeat.jobs.credits import UnmeteredLedger
from classbeat.jobs.runner import RunConfig, execute
from classbeat.playlists import common
from classbeat.songs import phase2, pick
from classbeat.songs.kind import PHASE_ONE
from classbeat.songs.phase2 import PHASE_TWO
from ctx import make_context
from optools import TOKEN_A
from songtools import CHOSEN, SongEnv, design_script, failing_script
from spotifyfake import CLIENT_A, USER_A

pytestmark = pytest.mark.usefixtures("songenv")

ITEMS = r"/v1/playlists/[0-9A-Za-z]{22}/items"


class Ledger(UnmeteredLedger):
    def __init__(self) -> None:
        self.committed: list[UUID] = []
        self.released: list[UUID] = []

    async def commit(self, db: Any, ctx: Any, job_id: UUID) -> None:
        self.committed.append(job_id)

    async def release(self, db: Any, ctx: Any, job_id: UUID) -> None:
        self.released.append(job_id)


async def settled(songenv: SongEnv, job_id: UUID) -> tuple[str, Ledger]:
    ledger = Ledger()
    await settle(songenv.factory, ledger)
    await settle(songenv.factory, ledger)  # once only
    return songenv.row(job_id).credit_state, ledger


def klass(songenv: SongEnv, class_id: UUID) -> Any:
    return songenv.sql("SELECT * FROM classes WHERE id = :id", id=class_id).one()


def uris(ids: list[str]) -> list[str | None]:
    return [f"spotify:track:{i}" for i in ids]


async def test_one_approve_gives_the_verified_private_playlist_and_the_first_design(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    job_id, revision_id = await songenv.approved(rider)
    pool_id = songenv.pool(rider.user_id).playlist_id
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    assert songenv.stages(job_id) == [*PHASE_ONE, *PHASE_TWO]
    # One model per stage group, from the job's route key: songs for the pick, design after it.
    models = design.current_runtime().models  # pyright: ignore[reportOptionalMemberAccess]
    assert isinstance(models, FactoryModels)
    assert models.asked == [("default", "songs"), ("default", "design")]

    [final] = songenv.class_playlists(rider.user_id)
    playlist = songenv.fake.playlists[final.playlist_id]
    # Private, not collaborative, the user's own, exactly the approved ids in order.
    assert (playlist.public, playlist.collaborative, playlist.owner) == (False, False, USER_A)
    assert playlist.entries == uris(CHOSEN)
    assert (final.class_id, final.revision_id) == (row.class_id, revision_id)
    assert final.state == "verified"
    assert (bytes(final.content_hash), final.track_count) == (common.ids_hash(CHOSEN), 8)
    assert final.detached_at is None
    # Attached in MOWL (after the pool) and analysed there; its MOWL id is recorded.
    assert [sid for _, sid in songenv.mowl.attaches] == [pool_id, final.playlist_id]
    assert final.mowl_playlist_id is not None

    # The first design: a draft on the approved list, adopted with the playlist as its music.
    shown = klass(songenv, row.class_id)
    draft = songenv.revision(shown.current_revision_id)
    assert (draft.kind, draft.job_id, draft.class_id) == ("draft", job_id, row.class_id)
    assert draft.music["song_list_id"] == str(revision_id)
    # The class views (#183) find the current draft's playlist by that key.
    found = songenv.sql(
        "SELECT p.id FROM classes c JOIN class_revisions r ON r.id = c.current_revision_id "
        "JOIN class_playlists p ON p.owner_id = r.owner_id AND p.class_id = r.class_id "
        "AND p.revision_id = CAST(r.music ->> 'song_list_id' AS uuid) "
        "AND p.detached_at IS NULL WHERE c.id = :c",
        c=row.class_id,
    ).all()
    assert [f.id for f in found] == [final.id]
    assert draft.music["source"] == "mowl_spotify_playlist"
    assert draft.provenance["origin"] == "design_with_songs"
    assert shown.music_source == {
        "source": "mowl_spotify_playlist",
        "spotify_playlist_id": final.playlist_id,
        "origin": "song_pick",
        "class_playlist_id": str(final.id),
    }
    # One create and one fill under the job's keys, nothing else written for the class.
    keys = sorted(o.idempotency_key for o in songenv.operations(rider.user_id) if o.class_id)
    assert keys == [f"{job_id}:final-create", f"{job_id}:final-fill"]
    assert songenv.creates() == 2  # the pool and the final playlist
    # The credit commits once, with the draft.
    state, ledger = await settled(songenv, job_id)
    assert (state, ledger.committed, ledger.released) == ("committed", [job_id], [])


def reorder_in_mowl(songenv: SongEnv, monkeypatch: pytest.MonkeyPatch, pool_id: str) -> None:
    listed = songenv.mowl._ids  # pyright: ignore[reportPrivateUsage]

    def reordered(spotify_playlist: str) -> list[str]:
        ids = listed(spotify_playlist)
        return ids if spotify_playlist == pool_id else [ids[1], ids[0], *ids[2:]]

    monkeypatch.setattr(songenv.mowl, "_ids", reordered)


@pytest.mark.parametrize("spoil", ["drop", "duplicate", "reorder"])
async def test_a_playlist_that_is_not_the_approved_list_fails_playlist_mismatch(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch, spoil: str
) -> None:
    rider = await songenv.rider()
    job_id, _ = await songenv.approved(rider)
    if spoil == "drop":  # the write keeps only 7 of the 8
        songenv.fake.inject("partial", method="PUT", path=ITEMS, keep=7)
    elif spoil == "duplicate":  # the read-back shows a copy of the first song in front
        songenv.fake.inject("duplicate", method="GET", path=ITEMS)
    else:  # MOWL lists the final playlist's first two songs the other way round
        reorder_in_mowl(songenv, monkeypatch, songenv.pool(rider.user_id).playlist_id)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "playlist_mismatch")
    assert "design" not in songenv.stages(job_id)
    assert klass(songenv, row.class_id).current_revision_id is None
    [final] = songenv.class_playlists(rider.user_id)
    # A failure after approval keeps what exists: the playlist and its row.
    assert final.playlist_id in songenv.fake.playlists
    assert final.detached_at is None
    assert final.state == ("verified" if spoil == "reorder" else "created")
    state, ledger = await settled(songenv, job_id)
    assert (state, ledger.committed, ledger.released) == ("released", [], [job_id])


async def test_a_retried_attempt_reuses_the_playlist_and_creates_no_second_one(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    job_id, _ = await songenv.approved(rider)
    songenv.script = failing_script("overloaded")  # model_unavailable after the playlist
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("queued", None)
    [first] = songenv.class_playlists(rider.user_id)
    assert first.state == "verified"
    writes = songenv.writes()
    songenv.script = design_script()
    row = await songenv.run(job_id)
    assert (row.state, row.attempt) == ("completed", 2)
    # The second attempt wrote nothing to Spotify: the verified playlist was reused.
    assert songenv.writes() == writes
    assert [p.id for p in songenv.class_playlists(rider.user_id)] == [first.id]
    assert klass(songenv, row.class_id).music_source["class_playlist_id"] == str(first.id)


@pytest.mark.parametrize("provider", ["spotify", "mowl"])
async def test_a_reconnect_between_approval_and_run_is_not_connected_with_no_write(
    songenv: SongEnv, provider: str
) -> None:
    rider = await songenv.rider()
    job_id, _ = await songenv.approved(rider)
    ctx = make_context(rider.user_id)
    if provider == "spotify":  # the same account again: a new generation
        await songenv.spot.seed(ctx, CLIENT_A, USER_A)
    else:
        await connections.save(songenv.factory, songenv.ring, ctx, "mowl", {"token": TOKEN_A})
    writes, attaches = songenv.writes(), len(songenv.mowl.attaches)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert songenv.stages(job_id)[-1] == "prepare"
    assert (songenv.writes(), len(songenv.mowl.attaches)) == (writes, attaches)
    assert songenv.class_playlists(rider.user_id) == []
    state, ledger = await settled(songenv, job_id)
    assert (state, ledger.released) == ("released", [job_id])


async def test_a_deleted_class_stops_the_job_at_the_fence(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider = await songenv.rider()
    job_id, _ = await songenv.approved(rider)
    class_id = songenv.row(job_id).class_id
    music = phase2._music  # pyright: ignore[reportPrivateUsage]

    async def then_delete(*args: Any, **kwargs: Any) -> Any:
        step = await music(*args, **kwargs)
        songenv.sql("DELETE FROM classes WHERE id = :id", id=class_id)  # the jobs cascade
        return step

    monkeypatch.setattr(phase2, "_music", then_delete)
    songenv.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)
    execution = await songenv.claim()
    assert execution is not None
    await execute(
        execution,
        songenv.factory,
        songenv.registry(),
        RunConfig(lease_seconds=songenv.spot.env.settings.job_lease_seconds),
        asyncio.Event(),
    )
    assert songenv.sql("SELECT count(*) FROM jobs WHERE id = :id", id=job_id).scalar_one() == 0
    assert songenv.sql(
        "SELECT count(*) FROM class_revisions WHERE class_id = :c", c=class_id
    ).scalar_one() == 0  # fmt: skip
    assert songenv.providers[-1].sent == []  # the design stage never started


async def test_a_reconnect_during_prepare_stops_before_any_write(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``prepare`` holds both connections in its fence: a Spotify reconnect after it opened the
    connection (here: while it opens MOWL's) ends the job there, before ``create``."""
    rider = await songenv.rider()
    job_id, _ = await songenv.approved(rider)
    opening = phase2.open_connection

    async def reconnecting(*args: Any, **kwargs: Any) -> Any:
        opened = await opening(*args, **kwargs)
        await songenv.spot.seed(make_context(rider.user_id), CLIENT_A, USER_A)
        return opened

    monkeypatch.setattr(phase2, "open_connection", reconnecting)
    writes = songenv.writes()
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert songenv.stages(job_id)[-1] == "prepare"
    assert songenv.writes() == writes


async def test_a_newer_list_while_designing_stores_no_draft(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final transaction checks again that the approved list is still the class's newest."""
    rider = await songenv.rider()
    job_id, _ = await songenv.approved(rider)
    class_id = songenv.row(job_id).class_id
    music = phase2._music  # pyright: ignore[reportPrivateUsage]

    async def then_supersede(*args: Any, **kwargs: Any) -> Any:
        step = await music(*args, **kwargs)
        songenv.sql(
            "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
            "content_hash, music, provenance) VALUES (:id, :o, :c, 99, 'song_list', "
            "CAST(:content AS jsonb), decode(repeat('ab', 32), 'hex'), '{}', '{}')",
            id=uuid.uuid4(), o=rider.user_id, c=class_id, content=json.dumps({"songs": []}),
        )  # fmt: skip
        return step

    monkeypatch.setattr(phase2, "_music", then_supersede)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert klass(songenv, class_id).current_revision_id is None
    assert songenv.sql(
        "SELECT count(*) FROM class_revisions WHERE class_id = :c AND kind = 'draft'", c=class_id
    ).scalar_one() == 0  # fmt: skip


async def test_a_spotify_reconnect_while_choosing_stores_no_list(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#198 item 14: the final store of phase 1 share-locks the Spotify connection again."""
    rider = await songenv.rider()
    choose = pick._choose  # pyright: ignore[reportPrivateUsage]

    async def reconnecting(*args: Any, **kwargs: Any) -> Any:
        chosen = await choose(*args, **kwargs)
        await songenv.spot.seed(make_context(rider.user_id), CLIENT_A, USER_A)
        return chosen

    monkeypatch.setattr(pick, "_choose", reconnecting)
    job_id = await songenv.created(rider)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert songenv.sql(
        "SELECT count(*) FROM class_revisions WHERE job_id = :id", id=job_id
    ).scalar_one() == 0  # fmt: skip


@pytest.mark.parametrize("phase", ["pick", "approved"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_the_music_step_is_closed_whether_the_store_ran_or_not(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch, phase: str, cancelled: bool
) -> None:
    """#122 item 4 for both phases: the runner's ``release`` drops the MOWL credentials once the
    attempt is over, also when a cancel seen by the final transaction means ``store`` never runs."""
    rider = await songenv.rider()
    module: Any = pick if phase == "pick" else phase2
    steps: list[Any] = []
    load, last_stage = module.load_music, module.run_stage
    job_id: UUID | None = None

    async def keep_step(*args: Any, **kwargs: Any) -> Any:
        steps.append(await load(*args, **kwargs))
        return steps[-1]

    async def then_cancel(*args: Any, **kwargs: Any) -> Any:
        outcome = await last_stage(*args, **kwargs)
        if cancelled and steps:  # after the music step: only the final transaction sees it
            songenv.sql("UPDATE jobs SET cancel_requested_at = now() WHERE id = :id", id=job_id)
        return outcome

    if phase == "pick":
        job_id = await songenv.created(rider)
    else:
        job_id, _ = await songenv.approved(rider)
    monkeypatch.setattr(module, "load_music", keep_step)
    monkeypatch.setattr(module, "run_stage", then_cancel)
    row = await songenv.run(job_id)
    expected = "waiting_for_approval" if phase == "pick" else "completed"
    assert row.state == ("cancelled" if cancelled else expected)
    [step] = steps
    assert step._opened is None  # pyright: ignore[reportPrivateUsage]
