"""A class on the user's own Spotify playlist (roadmap Amendment 26, ADR 0008 amendment of
2026-11-16): ``design_with_songs`` with ``source``. The playlist's tracks in their order become
the ``song_list`` the user approves; phase 2 makes a **new** private playlist in that order. The
user's playlist is only read. Against the fake Spotify, the fake MOWL and ``FakeProvider``; no
network, no real account."""

import json
import secrets
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from classbeat import songs
from classbeat.design.interpretation_row import handoff_key
from classbeat.playlists import common
from jobtools import new_class, new_job
from revisesongtools import change, current
from songtools import BRIEF, Rider, SongEnv, design_script, song_id
from spotifyfake import USER_A

pytestmark = pytest.mark.usefixtures("songenv", "no_stored_flag")


@pytest.fixture
def no_stored_flag(songenv: SongEnv) -> Iterator[None]:
    """The stored ``song_picking`` flag is shared by the database: none before and after."""
    songenv.sql("DELETE FROM app_settings WHERE key = 'song_picking'")
    yield
    songenv.sql("DELETE FROM app_settings WHERE key = 'song_picking'")


ORDER = [5, 3, 11, 0, 7, 2, 9, 14]  # 8 songs of 240 s: the design script's 30 minutes
OWN = [song_id(i) for i in ORDER]
ITEMS = r"/v1/playlists/[0-9A-Za-z]{22}/items"


def own_playlist(songenv: SongEnv, ids: list[str] | None = None) -> Any:
    """The rider's own playlist in the fake Spotify (not one classbeat made)."""
    entries = [f"spotify:track:{i}" for i in (OWN if ids is None else ids)]
    return songenv.fake.add_playlist(USER_A, "My Tuesday ride", entries)


def source(playlist: Any) -> dict[str, str]:
    return {"source": "mowl_spotify_playlist", "spotify_playlist_id": playlist.id}


def untouched(songenv: SongEnv, playlist: Any, entries: list[str | None]) -> None:
    """The user's playlist: the same items and version, and never a write request naming it."""
    assert (playlist.entries, playlist.version) == (entries, 1)
    named = [s for s in songenv.api() if playlist.id in s.path]
    assert all(s.method == "GET" for s in named), [(s.method, s.path) for s in named]


def refused(response: Any, reason: str, status: int = 409) -> None:
    assert (response.status_code, response.json()) == (status, {"detail": {"reason": reason}})


async def test_the_playlist_is_the_list_to_approve_with_no_model_no_pool_and_no_write(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    mine = own_playlist(songenv)
    entries = list(mine.entries)
    job_id, revision_id, digest = await songenv.waiting(rider, source=source(mine))
    assert songenv.stages(job_id) == ["pool_analyse"]
    assert songenv.kinds(job_id)[-2:] == ["revision_created", "waiting_for_approval"]
    assert songenv.counts()[2] == 0  # no model turn
    assert songenv.writes() == 0  # nothing written to Spotify
    assert songenv.pool(rider.user_id).playlist_id is None  # the pool is never made or touched
    stored = songenv.revision(revision_id)
    assert stored.owner_id == rider.user_id
    content = stored.content
    assert [s["spotify_id"] for s in content["songs"]] == OWN  # the playlist's order
    assert [s["role"] for s in content["songs"]] == ["warmup", *["interval"] * 6, "cooldown"]
    assert {s["new"] for s in content["songs"]} == {False}
    assert (content["mode"], content["target_sec"], content["total_sec"]) == ("pick", 1800, 1857)
    # The approval hash is the stored hash, and it covers the ids in their order.
    assert bytes(stored.content_hash).hex() == digest
    assert stored.music["fingerprint"] == common.ids_hash(OWN).hex()
    assert stored.music["spotify_user_id"] == USER_A
    assert stored.provenance["music_from"] == "spotify_playlist"
    # Attached in MOWL as design_draft would (a MOWL link, not a Spotify write).
    assert [sid for _, sid in songenv.mowl.attaches] == [mine.id]
    untouched(songenv, mine, entries)


async def test_after_approve_a_new_private_playlist_in_class_order_is_the_class_music(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    mine = own_playlist(songenv)
    entries = list(mine.entries)
    job_id, revision_id = await songenv.approved(rider, source=source(mine))
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    [final] = songenv.class_playlists(rider.user_id)
    assert final.playlist_id != mine.id
    assert (final.owner_id, final.revision_id, final.state) == (
        rider.user_id,
        revision_id,
        "verified",
    )
    made = songenv.fake.playlists[final.playlist_id]
    assert (made.public, made.collaborative, made.owner) == (False, False, USER_A)
    assert made.entries == [f"spotify:track:{i}" for i in OWN]
    assert songenv.creates() == 1  # only the class's own playlist, no pool
    keys = sorted(o.idempotency_key for o in songenv.operations(rider.user_id) if o.class_id)
    assert keys == [f"{job_id}:final-create", f"{job_id}:final-fill"]
    shown = songenv.sql("SELECT * FROM classes WHERE id = :c", c=row.class_id).one()
    assert shown.music_source["spotify_playlist_id"] == final.playlist_id
    assert [sid for _, sid in songenv.mowl.attaches] == [mine.id, final.playlist_id]
    untouched(songenv, mine, entries)


async def test_a_replayed_approve_and_a_retried_attempt_make_one_playlist(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    mine = own_playlist(songenv)
    job_id, revision_id, digest = await songenv.waiting(rider, source=source(mine))
    refused(await rider.approve(job_id, revision_id, "0" * 64), "hash_mismatch")
    assert (await rider.approve(job_id, revision_id, digest)).status_code == 200
    again = await rider.approve(job_id, revision_id, digest)
    assert (again.status_code, again.json()["state"]) == (200, "queued")
    assert songenv.kinds(job_id).count("approved") == 1
    songenv.script = design_script()
    assert (await songenv.run(job_id)).state == "completed"
    after = await rider.approve(job_id, revision_id, digest)
    assert (after.status_code, after.json()["state"]) == (200, "completed")
    assert len(songenv.class_playlists(rider.user_id)) == 1
    assert songenv.creates() == 1


async def test_another_user_can_neither_approve_nor_design_on_the_list(songenv: SongEnv) -> None:
    alice = await songenv.rider()
    mine = own_playlist(songenv)
    job_id, revision_id, digest = await songenv.waiting(alice, source=source(mine))
    bob = await songenv.rider()
    refused(await bob.approve(job_id, revision_id, digest), "not_found", 404)
    row = songenv.row(job_id)
    taken = await bob.create(class_id=str(row.class_id), source=source(mine))
    refused(taken, "class_not_found", 404)
    assert songenv.row(job_id).state == "waiting_for_approval"
    assert songenv.writes() == 0


def flag_off(songenv: SongEnv, how: str) -> None:
    """The kill switch of classbeat's Spotify writes: the environment switch
    (``CLASSBEAT_SONG_PICKING``) or the stored ``song_picking`` flag ``off``."""
    if how == "environment":
        songs.configure(songs.SongConfig(enabled=False, sync_wait_seconds=0.3))
    else:
        songenv.sql(
            "INSERT INTO app_settings (key, value) VALUES ('song_picking', CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            v=json.dumps("off"),
        )


async def test_the_pool_consent_is_not_needed_but_the_song_picking_flag_is(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider(consent=False)  # no pool consent: the pool is never touched
    mine = own_playlist(songenv)
    refused(await rider.create(), "consent_missing")  # a Claude pick still needs it
    job_id, _, _ = await songenv.waiting(rider, source=source(mine))
    assert songenv.row(job_id).owner_id == rider.user_id
    # Without the ``song_picking`` feature (the flag is ``granted``), refused like a pick.
    other = await songenv.rider()
    songenv.sql("DELETE FROM entitlements WHERE owner_id = :o", o=other.user_id)
    refused(await other.create(source=source(mine)), "song_picking_off")
    assert songenv.writes() == 0


@pytest.mark.parametrize("how", ["environment", "stored"])
async def test_the_kill_switch_refuses_a_source_job_and_sends_the_handoff_to_design_draft(
    songenv: SongEnv, how: str
) -> None:
    rider = await songenv.rider()
    mine = own_playlist(songenv)
    class_id, _ = interpreted(songenv, rider, source(mine))
    flag_off(songenv, how)
    refused(await rider.create(source=source(mine)), "song_picking_off")
    made = await rider.browser.post(f"/api/classes/{class_id}/design-from-interpretation", None)
    assert made.status_code == 201, made.text
    assert made.json()["kind"] == "design_draft"  # the write-free design, as before
    row = songenv.row(UUID(made.json()["job_id"]))
    assert row.input["music"] == source(mine)
    assert songenv.writes() == 0


@pytest.mark.parametrize("spoil", ["needs_reconnect", "no_account"])
async def test_a_spotify_row_without_a_usable_account_is_not_connected_at_admit(
    songenv: SongEnv, spoil: str
) -> None:
    """The source path's own ``spotify_account_in`` check: the row is still the user's current
    Spotify connection (the jobs API's generic check passes), but not usable."""
    rider = await songenv.rider()
    mine = own_playlist(songenv)
    if spoil == "needs_reconnect":
        change = "status = 'needs_reconnect'"
    else:
        change = "public = public - 'spotify_user_id'"
    songenv.sql(
        f"UPDATE connections SET {change} WHERE id = :id",  # noqa: S608 - fixed test SQL
        id=rider.spotify_connection_id,
    )
    refused(await rider.create(source=source(mine)), "not_connected")
    assert songenv.writes() == 0


async def failed_after_approve(songenv: SongEnv, rider: Rider, mine: Any) -> tuple[UUID, UUID]:
    """A source job approved whose fill writes 7 of the 8 songs: ``playlist_mismatch``."""
    job_id, revision_id = await songenv.approved(rider, source=source(mine))
    songenv.fake.inject("partial", method="PUT", path=ITEMS, keep=7)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "playlist_mismatch")
    return row.class_id, revision_id


async def test_a_failed_source_job_resumes_under_the_same_gate_without_the_consent(
    songenv: SongEnv,
) -> None:
    """A resume (``mode = "resume"``, no ``source``) needs the flag and Spotify, as the source job
    did, and no pool consent: the user it admitted can always finish the class."""
    rider = await songenv.rider(consent=False)
    mine = own_playlist(songenv)
    entries = list(mine.entries)
    class_id, revision_id = await failed_after_approve(songenv, rider, mine)
    songenv.script = design_script()
    resumed = await rider.resume(class_id, revision_id)
    assert resumed.status_code == 201, resumed.text
    row = await songenv.run(UUID(resumed.json()["id"]))
    assert (row.state, row.error_category) == ("completed", None)
    final = [p for p in songenv.class_playlists(rider.user_id) if p.state == "verified"]
    assert [songenv.fake.playlists[p.playlist_id].entries for p in final] == [
        [f"spotify:track:{i}" for i in OWN]
    ]
    untouched(songenv, mine, entries)


@pytest.mark.parametrize("how", ["environment", "stored"])
async def test_the_kill_switch_also_stops_the_resume(songenv: SongEnv, how: str) -> None:
    rider = await songenv.rider()
    mine = own_playlist(songenv)
    class_id, revision_id = await failed_after_approve(songenv, rider, mine)
    flag_off(songenv, how)
    refused(await rider.resume(class_id, revision_id), "song_picking_off")


async def test_changing_songs_of_a_source_class_is_a_claude_pick_with_its_gates(
    songenv: SongEnv,
) -> None:
    """``revise_songs`` on the class picks new songs with Claude: it needs the flag and the pool
    consent, like any pick."""
    rider = await songenv.rider(consent=False)
    mine = own_playlist(songenv)
    job_id, _ = await songenv.approved(rider, source=source(mine))
    row = await songenv.run(job_id)
    assert row.state == "completed"
    base = current(songenv, row.class_id)
    refused(await change(rider, row.class_id, base), "consent_missing")
    flag_off(songenv, "stored")
    refused(await change(rider, row.class_id, base), "song_picking_off")


async def test_a_job_on_the_users_playlist_does_not_hold_the_pool(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    mine = own_playlist(songenv)
    first = await rider.create(source=source(mine))
    assert first.status_code == 201, first.text  # queued, not run
    assert (await rider.create()).status_code == 201  # a pick is not pool_busy


@pytest.mark.parametrize(
    "extra",
    [
        {"mode": "resume", "resume": {"song_list_revision_id": str(uuid.uuid4())}},
        {"seed": {"song_list_revision_id": str(uuid.uuid4()), "replace": [0]}},
        {"source": {"source": "mowl_spotify_playlist", "spotify_playlist_id": "short"}},
        {"source": {"source": "mowl_swedebeat_mix", "mix_id": "m1"}},
    ],
)
async def test_a_source_with_a_resume_or_a_seed_or_no_playlist_id_is_refused(
    songenv: SongEnv, extra: dict[str, Any]
) -> None:
    rider = await songenv.rider()
    mine = own_playlist(songenv)
    response = await rider.create(
        class_id=str(new_class(songenv.admin, rider.user_id)),
        **{
            "source": source(mine),
            **extra,
        },
    )
    assert response.status_code == 422, response.text


async def test_a_playlist_with_no_usable_track_fails_and_writes_nothing(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    mine = own_playlist(songenv, OWN[:2])
    songenv.mowl.no_bpm = set(OWN[:2])
    job_id = await songenv.created(rider, source=source(mine))
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert songenv.writes() == 0
    untouched(songenv, mine, [f"spotify:track:{i}" for i in OWN[:2]])


# --- the hand-off (ADR 0029 section 5) --------------------------------------------------------


def interpreted(songenv: SongEnv, rider: Rider, music: dict[str, str]) -> tuple[UUID, UUID]:
    """A class with a stored interpretation and its completed interpret job (as the worker
    leaves them): (class id, interpret job id)."""
    class_id = new_class(songenv.admin, rider.user_id)
    given = {
        "request": "A 30 minute class with one climb and two sprints",
        "title": "Tuesday climb",
        "mowl_connection_id": str(rider.mowl_connection_id),
        "spotify_connection_id": str(rider.spotify_connection_id),
    }
    job_id = new_job(
        songenv.admin,
        rider.user_id,
        class_id,
        kind="interpret_request",
        state="completed",
        input=json.dumps(given),
        credit_state="none",
        finished_at=datetime.now(UTC),
        idempotency_key=secrets.token_urlsafe(18),
    )
    stored = {"v": 1, "job_id": str(job_id), "brief": BRIEF, "music": music, "items": []}
    songenv.sql(
        "UPDATE classes SET interpretation = CAST(:v AS jsonb) WHERE id = :c",
        v=json.dumps(stored),
        c=class_id,
    )
    return class_id, job_id


async def test_the_handoff_enqueues_the_pick_pipeline_on_the_users_playlist(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    mine = own_playlist(songenv)
    entries = list(mine.entries)
    class_id, interpret_id = interpreted(songenv, rider, source(mine))
    path = f"/api/classes/{class_id}/design-from-interpretation"
    made = await rider.browser.post(path, None)
    assert made.status_code == 201, made.text
    assert (made.json()["kind"], made.json()["state"]) == ("design_with_songs", "queued")
    again = await rider.browser.post(path, None)
    assert (again.status_code, again.json()) == (200, made.json())  # one job, the same key
    job_id = UUID(made.json()["job_id"])
    row = songenv.row(job_id)
    assert (row.owner_id, row.class_id, row.idempotency_key) == (
        rider.user_id,
        class_id,
        handoff_key(interpret_id),
    )
    assert row.input["source"] == source(mine)
    assert row.input["spotify_connection_id"] == str(rider.spotify_connection_id)
    waited = await songenv.run(job_id)
    assert waited.state == "waiting_for_approval"
    listed = songenv.revision(waited.approval_revision_id).content["songs"]
    assert [s["spotify_id"] for s in listed] == OWN
    untouched(songenv, mine, entries)
