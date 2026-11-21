"""``studio_songs`` (ADR 0036 sections 2 and 3; SR C3): the studio's song stage on the fake Spotify,
the fake SwedeBeat gateway and ``FakeProvider``, with **no MOWL connection**: the facts from one
gateway match, the evidence retry, swaps with their records, undo to a gap, and the approval.
No network, no real account."""

import hashlib
import json
import re
from typing import Any
from uuid import UUID

from classbeat.db import user_session
from classbeat.design.revision import canonical
from classbeat.playlists import common
from classbeat.songs.admit import pool_busy
from classbeat.swedebeat_studio import config as studio_config
from ctx import make_context
from gatewaycore import reference_of
from pickfake import POOL_14
from songtools import song_id
from studiotools import (
    KIND,
    LIST_8,
    StudioEnv,
    artist,
    choose_turns,
    pool_turns,
    script,
    title,
)

ALL_POOL = [ref for ref, _ in POOL_14]


def _content(studio: StudioEnv, revision_id: UUID) -> dict[str, Any]:
    stored = studio.songs.revision(revision_id)
    return stored.content


def _sent(studio: StudioEnv) -> str:
    parts: list[str] = []
    for provider in studio.songs.providers:
        for sent in provider.sent:
            parts += [sent.spec.system, sent.spec.first_message]
            for results, reminder in sent.turns:
                parts += [result.content for result in results]
                parts.append(reminder or "")
    return "\n".join(parts)


async def test_a_studio_pick_takes_its_facts_from_one_match_with_no_mowl(
    studio: StudioEnv,
) -> None:
    studio.found(*ALL_POOL)
    rider = await studio.rider()
    job_id, revision_id, digest = await studio.waiting(rider)
    songs = studio.songs
    assert songs.stages(job_id) == ["pool_search", "pool_match", "choose", "working_copy"]
    stored = songs.revision(revision_id)
    content = stored.content
    # The hash binds the content with every reference, duration and BPM.
    assert bytes(stored.content_hash) == hashlib.sha256(canonical(content)).digest()
    assert bytes(stored.content_hash).hex() == digest
    assert (content["v"], content["source"], content["mode"]) == (1, "swedebeat_studio", "pick")
    assert content["evidence"] == {
        "state": "ok",
        "sent": 14,
        "missing_first": 0,
        "missing": 0,
        "anyway": False,
    }
    chosen = [song_id(i) for _, i, _ in LIST_8]
    assert [s["spotify_id"] for s in content["songs"]] == chosen
    first = content["songs"][0]
    assert first["swedebeat"] == {
        "reference": reference_of(title(0), artist(0)),
        "title": title(0),
        "artist": artist(0),
        "duration_sec": 240,  # SwedeBeat's, never MOWL's
        "bpm": 128.0,
        "score": 0.93,
    }
    assert (first["gap"], first["swap"]) == (False, None)
    assert content["total_sec"] == 8 * 240  # whole songs: no crossfade is subtracted (B6)
    assert content["crossfade_sec"] == 0
    # The stored total is the stage's: the plain sum of the SwedeBeat durations (#1371), and the
    # choose stage showed the model no crossfade.
    assert content["total_sec"] == sum(s["swedebeat"]["duration_sec"] for s in content["songs"])
    assert re.search(r'"crossfade_sec":\s*0(\.0)?[,}]', _sent(studio))
    assert stored.music["source"] == "swedebeat_studio"
    assert stored.music["fingerprint"] == common.ids_hash(chosen).hex()
    assert stored.music["swedebeat_connection_id"] == str(rider.swedebeat_connection_id)
    # One match for the 14 candidates (all found: no retry).
    assert len(studio.matches()) == 1
    assert len(studio.matches()[0].body["tracks"]) == 14
    # The working copy (the pool playlist) holds the list, in order; MOWL saw nothing.
    pool = songs.pool(rider.user_id)
    assert songs.fake.playlists[pool.playlist_id].entries == [f"spotify:track:{i}" for i in chosen]
    assert songs.mowl.requests == []
    # No reference, track id or playlist id reached a model.
    sent = _sent(studio)
    for value in [*chosen, pool.playlist_id, reference_of(title(0), artist(0)), "rider-one"]:
        assert value not in sent
    # The approval binds the hash; the approved job completes with nothing written.
    answer = await rider.browser.approve(job_id, revision_id, digest)
    assert answer.status_code == 200, answer.text
    row = await studio.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)


async def test_a_transient_per_track_miss_keeps_the_song(studio: StudioEnv) -> None:
    studio.found(*ALL_POOL)
    studio.gateway.add_flaky(title(4), artist(4))  # the climb LIST_8 picks
    rider = await studio.rider()
    _, revision_id, _ = await studio.waiting(rider)
    content = _content(studio, revision_id)
    assert content["evidence"] == {
        "state": "ok",
        "sent": 14,
        "missing_first": 1,
        "missing": 0,
        "anyway": False,
    }
    assert [s["spotify_id"] for s in content["songs"]] == [song_id(i) for _, i, _ in LIST_8]
    assert all(s["swap"] is None for s in content["songs"])
    first, second = studio.matches()
    assert len(first.body["tracks"]) == 14
    assert second.body["tracks"] == [{"title": title(4), "artist": artist(4)}]


async def test_all_missing_gives_no_swaps_and_cannot_be_approved(studio: StudioEnv) -> None:
    studio.songs.script = script(pool_turns(("g0", "g1"), POOL_14))  # no swap, no choice
    rider = await studio.rider()
    job_id, revision_id, digest = await studio.waiting(rider)
    assert studio.songs.stages(job_id) == ["pool_search", "pool_match", "working_copy"]
    content = _content(studio, revision_id)
    assert content["evidence"] == {
        "state": "suspected_unavailable",
        "sent": 14,
        "missing_first": 14,
        "missing": 14,
        "anyway": False,
    }
    assert content["rounds"] == 0
    assert all(s["gap"] and s["swedebeat"] is None for s in content["songs"])
    assert len(studio.matches()) == 2  # the first answer and the one retry, nothing more
    answer = await rider.browser.approve(job_id, revision_id, digest)
    assert answer.status_code == 409
    assert answer.json()["detail"]["reason"] == "list_has_gap"
    view = await rider.browser.get(
        f"/api/classes/{_class(studio, job_id)}/studio-songs/{revision_id}"
    )
    assert view.status_code == 200
    assert view.json()["evidence"]["state"] == "suspected_unavailable"
    assert view.json()["approvable"] is False


def _class(studio: StudioEnv, job_id: UUID) -> UUID:
    return studio.songs.row(job_id).class_id


# 70 % missing: tracks 0, 2, 6 and 12 of the pool are found, the other ten are not. The swap
# round searches g2 and g3 (tracks 20 to 39, all found) for the roles the list lacks.
FOUND_4 = (0, 2, 6, 12)
SWAP_12 = [
    (0, "climb"), (1, "climb"), (2, "climb"), (3, "sprint"), (4, "sprint"), (5, "sprint"),
    (6, "warmup"), (7, "warmup"), (8, "warmup"), (9, "build"), (10, "build"), (11, "build"),
]  # fmt: skip
# The usable pool: 0 t0, 1 t2, 2 t6, 3 t12, then the swap's 4 t20 ... 15 t31.
SWAPPED_LIST = [
    ("pool", 10, "warmup"), ("pool", 1, "build"), ("pool", 4, "climb"), ("pool", 13, "build"),
    ("pool", 2, "sprint"), ("pool", 7, "sprint"), ("pool", 5, "climb"), ("pool", 3, "cooldown"),
]  # fmt: skip


def _swap_script() -> dict[str, Any]:
    return script(
        pool_turns(("g0", "g1"), POOL_14),
        pool_turns(("g2", "g3"), SWAP_12),
        choose_turns(SWAPPED_LIST),
    )


async def test_seventy_percent_missing_still_swaps_and_records_each_swap(
    studio: StudioEnv,
) -> None:
    studio.found(*FOUND_4, *range(20, 40))
    studio.songs.script = _swap_script()
    rider = await studio.rider()
    job_id, revision_id, _ = await studio.waiting(rider)
    stages = studio.songs.stages(job_id)
    assert stages == ["pool_search", "pool_match", "swap", "choose", "working_copy"]
    content = _content(studio, revision_id)
    assert content["evidence"]["state"] == "ok"
    assert (content["evidence"]["missing"], content["rounds"]) == (10, 1)
    swaps = {s["spotify_id"]: s["swap"] for s in content["songs"] if s["swap"] is not None}
    # The climb, the sprint, then a warmup and a build the list needs (to_replace's order).
    assert {sid: s["original"]["spotify_id"] for sid, s in swaps.items()} == {
        song_id(20): song_id(4),
        song_id(23): song_id(7),
        song_id(26): song_id(1),
        song_id(29): song_id(3),
    }
    record = swaps[song_id(20)]
    assert record == {
        "original": {"spotify_id": song_id(4), "title": title(4), "artist": artist(4),
                     "cover": record["original"]["cover"]},
        "replacement": {"title": title(20), "artist": artist(20)},
        "reason": "missing",
        "score": 0.93,
        "undone": False,
    }  # fmt: skip
    # Each new candidate was matched again: 14 (+ the retry of the 10) and then the 12.
    sent = [len(m.body["tracks"]) for m in studio.matches()]
    assert sent == [14, 10, 12]
    assert studio.songs.mowl.requests == []


async def test_a_swap_can_be_undone_and_the_gap_never_approved(studio: StudioEnv) -> None:
    studio.found(*FOUND_4, *range(20, 40))
    studio.songs.script = _swap_script()
    rider = await studio.rider()
    job_id, revision_id, _ = await studio.waiting(rider)
    class_id = _class(studio, job_id)
    view = (await rider.browser.get(f"/api/classes/{class_id}/studio-songs/{revision_id}")).json()
    assert view["approvable"] is True
    assert [s["can_undo"] for s in view["songs"]] == [
        True, False, True, True, False, True, False, False
    ]  # fmt: skip
    assert "spotify_id" not in json.dumps(view)
    assert "reference" not in json.dumps(view)
    # Undo the first song (track 26 swapped in for track 1): one step, the wait cancelled.
    answer = await rider.act(class_id, revision_id, "undo", 0)
    assert answer.status_code == 201, answer.text
    assert studio.songs.row(job_id).state == "cancelled"
    undo_id = UUID(answer.json()["job_id"])
    before = len(studio.matches())
    row = await studio.run(undo_id)
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    assert len(studio.matches()) == before  # no catalogue call for an undo
    undone = studio.songs.revision(row.approval_revision_id)
    assert undone.parent_id == revision_id
    gap = undone.content["songs"][0]
    assert (gap["gap"], gap["swedebeat"], gap["spotify_id"]) == (True, None, song_id(1))
    assert gap["swap"]["undone"] is True
    assert undone.content["total_sec"] == 7 * 240
    assert undone.content["crossfade_sec"] == 0
    # The working copy follows the list, the original back in its slot.
    pool = studio.songs.pool(rider.user_id)
    entries = studio.songs.fake.playlists[pool.playlist_id].entries
    assert entries[0] == f"spotify:track:{song_id(1)}"
    # A gap can never be approved into a mix.
    digest = bytes(row.approval_hash).hex()
    refused = await rider.browser.approve(undo_id, row.approval_revision_id, digest)
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "list_has_gap"
    # Undo again on the same slot is refused, and the wait stays as it was.
    again = await rider.act(class_id, row.approval_revision_id, "undo", 0)
    assert again.status_code == 422, again.text
    assert studio.songs.row(undo_id).state == "waiting_for_approval"


async def test_the_approval_hash_binds_the_references(studio: StudioEnv) -> None:
    studio.found(*ALL_POOL)
    rider = await studio.rider()
    job_id, revision_id, digest = await studio.waiting(rider)
    studio.songs.sql(
        "UPDATE class_revisions SET content = jsonb_set(content, "
        "'{songs,0,swedebeat,reference}', '\"sb-ref-swapped\"') WHERE id = :id",
        id=revision_id,
    )
    answer = await rider.browser.approve(job_id, revision_id, digest)
    assert answer.status_code == 409
    assert answer.json()["detail"]["reason"] == "hash_mismatch"


async def test_the_studio_off_refuses_before_anything_is_written(studio: StudioEnv) -> None:
    rider = await studio.rider()
    studio_config.configure(None)
    answer = await rider.create()
    assert answer.status_code == 409
    assert answer.json()["detail"]["reason"] == "feature_off"
    assert studio.songs.sql("SELECT count(*) FROM jobs WHERE kind = :k", k=KIND).scalar() == 0
    # A queued job whose worker has the studio off ends feature_off before any call.
    studio_config.configure(studio_config.StudioConfig(frozenset({"design"})))
    job_id = await studio.created(rider)
    studio_config.configure(None)
    row = await studio.run(job_id)
    assert (row.state, row.error_category) == ("failed", "feature_off")
    assert studio.matches() == []
    assert all(seen.path != "/v1/search" for seen in studio.songs.api())


ANOTHER_6 = [(ref, "warmup") for ref in range(6)]
# The kept songs of the undone list, in their slots 1 to 7, then the new warmup in slot 0.
KEPT_ROLES = ["build", "climb", "build", "sprint", "sprint", "climb", "cooldown"]
ANOTHER_LIST = [("pool", 0, "warmup")] + [("kept", i, r) for i, r in enumerate(KEPT_ROLES)]


async def test_another_song_for_a_gap_is_matched_recorded_and_approvable(
    studio: StudioEnv,
) -> None:
    studio.found(*FOUND_4, *range(20, 40))
    studio.songs.script = _swap_script()
    rider = await studio.rider()
    job_id, revision_id, _ = await studio.waiting(rider)
    class_id = _class(studio, job_id)
    undo_id = UUID((await rider.act(class_id, revision_id, "undo", 0)).json()["job_id"])
    undone = await studio.run(undo_id)
    studio.songs.script = script(pool_turns(("g3",), ANOTHER_6), choose_turns(ANOTHER_LIST))
    answer = await rider.act(class_id, undone.approval_revision_id, "another", 0)
    assert answer.status_code == 201, answer.text
    another_id = UUID(answer.json()["job_id"])
    row = await studio.run(another_id)
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    assert studio.songs.stages(another_id) == [
        "pool_search", "pool_match", "choose", "working_copy"
    ]  # fmt: skip
    content = studio.songs.revision(row.approval_revision_id).content
    first = content["songs"][0]
    assert (first["spotify_id"], first["gap"]) == (song_id(30), False)
    assert first["swap"]["original"]["spotify_id"] == song_id(1)  # the slot's first original
    assert (first["swap"]["reason"], first["swap"]["undone"]) == ("asked", False)
    undone_songs = studio.songs.revision(undone.approval_revision_id).content["songs"]
    assert content["songs"][1:] == undone_songs[1:]  # the other songs kept in their slots
    assert studio.matches()[-1].body["tracks"][0] == {"title": title(30), "artist": artist(30)}
    approved = await rider.browser.approve(
        another_id, row.approval_revision_id, bytes(row.approval_hash).hex()
    )
    assert approved.status_code == 200, approved.text


SHORT_BRIEF = {"length_min": 12, "intensity": "moderate"}  # three songs of 240 s: 720 s
POOL_6 = [
    (0, "warmup"),
    (1, "warmup"),
    (2, "build"),
    (3, "climb"),
    (4, "cooldown"),
    (5, "cooldown"),
]
SWAP_9 = [(r, "warmup") for r in range(4)] + [(4, "cooldown"), (5, "cooldown")]
SWAP_9 += [(r, "build") for r in (6, 7, 8)]
LIST_3 = [("pool", 0, "warmup"), ("pool", 6, "build"), ("pool", 4, "cooldown")]


async def test_swap_anyway_is_the_users_choice_after_a_suspected_list(studio: StudioEnv) -> None:
    studio.found(*range(20, 30))  # the first ten tracks are not in the catalogue
    studio.songs.script = script(pool_turns(("g0",), POOL_6))
    rider = await studio.rider()
    job_id, revision_id, _ = await studio.waiting(rider, brief=SHORT_BRIEF)
    assert _content(studio, revision_id)["evidence"]["state"] == "suspected_unavailable"
    class_id = _class(studio, job_id)
    # Undo and another are not offered on a suspected list.
    assert (await rider.act(class_id, revision_id, "another", 0)).status_code == 422
    assert studio.songs.row(job_id).state == "waiting_for_approval"  # the refusal rolled back
    studio.songs.script = script(
        pool_turns(("g0",), POOL_6), pool_turns(("g2",), SWAP_9), choose_turns(LIST_3)
    )
    answer = await rider.act(class_id, revision_id, "swap-anyway")
    assert answer.status_code == 201, answer.text
    assert studio.songs.row(job_id).state == "cancelled"
    anyway = await studio.run(UUID(answer.json()["job_id"]))
    assert anyway.state == "waiting_for_approval", (anyway.state, anyway.error_category)
    content = studio.songs.revision(anyway.approval_revision_id).content
    # The match was suspected again; the list is ok by the user's choice, and says so.
    assert content["evidence"] == {
        "state": "ok", "sent": 6, "missing_first": 6, "missing": 6, "anyway": True
    }  # fmt: skip
    assert content["rounds"] == 1
    assert [s["spotify_id"] for s in content["songs"]] == [song_id(i) for i in (20, 26, 24)]
    originals = {s["spotify_id"]: s["swap"]["original"]["spotify_id"]
                 for s in content["songs"] if s["swap"]}  # fmt: skip
    assert originals == {song_id(20): song_id(0), song_id(24): song_id(4)}
    # Whole songs: 720 s for a 720 s class, no crossfade subtracted.
    assert content["total_sec"] == 3 * 240


async def test_another_users_list_is_not_found(studio: StudioEnv) -> None:
    studio.found(*ALL_POOL)
    rider = await studio.rider()
    job_id, revision_id, _ = await studio.waiting(rider)
    class_id = _class(studio, job_id)
    other = await studio.rider()
    path = f"/api/classes/{class_id}/studio-songs/{revision_id}"
    assert (await other.browser.get(path)).status_code == 404
    assert (await other.act(class_id, revision_id, "swap-anyway")).status_code == 404
    assert studio.songs.row(job_id).state == "waiting_for_approval"
    # With the studio off, the routes answer like an unknown path.
    studio_config.configure(None)
    assert (await rider.browser.get(path)).status_code == 404
    assert (await rider.act(class_id, revision_id, "undo", 0)).status_code == 404


async def test_a_queued_studio_pick_holds_the_pool_for_every_pick(studio: StudioEnv) -> None:
    rider = await studio.rider()
    async with user_session(studio.songs.factory, make_context(rider.user_id)) as db:
        assert not await pool_busy(db, rider.user_id)
    await studio.created(rider)  # queued: it will write the pool (the working copy)
    async with user_session(studio.songs.factory, make_context(rider.user_id)) as db:
        assert await pool_busy(db, rider.user_id)  # a song pick of any class is refused


async def test_the_jobs_plan_gates_reach_the_song_stage_through_the_kind(
    studio: StudioEnv,
) -> None:
    """The recorded list (1920 s) is whole songs inside the instructor plan, but not inside a
    30-minute plan's bound (1890 s less 1 s a song): the job's stored ``plan_gates`` decide, so the
    kind must hand them to ``Steps`` (a mutation dropping ``given.plan_gates`` fails this)."""
    studio.found(*ALL_POOL)
    rider = await studio.rider()
    job_id = await studio.created(rider)
    studio.songs.sql(
        "UPDATE jobs SET input = jsonb_set(input, '{plan_gates}', "
        '\'{"max_class_minutes": 30, "games": false, "races": false, "ftp_tests": false}\') '
        "WHERE id = :id",
        id=job_id,
    )
    row = await studio.run(job_id)
    assert row.state == "failed"
    assert "choose" in studio.songs.stages(job_id)
