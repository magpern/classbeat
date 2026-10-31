"""``revise_songs`` (ADR 0008 section 6; issue SJ3): "Change songs" in a revision, against the
fake Spotify, the fake MOWL and ``FakeProvider``. No network, no real account."""

import json
import uuid
from typing import Any
from uuid import UUID

import pytest

from agentfake import FactoryModels
from classbeat.agent import model_input
from classbeat.agent.revise import SYSTEM_PROMPT
from classbeat.design import kind as design
from classbeat.design import limits as design_limits
from classbeat.songs import pick
from revisesongtools import (
    REQUEST,
    change,
    changed_approved,
    changed_waiting,
    current,
    picked,
    resume_change,
    revise_script,
)
from songtools import CHOSEN, SongEnv, design_script, failing_script, song_id

pytestmark = pytest.mark.usefixtures("songenv")

NEW = song_id(13)  # pool ref 0 of ``replace_script``
CHANGED = [*CHOSEN[:2], NEW, *CHOSEN[3:]]


def refused(response: Any, reason: str, status: int = 409) -> None:
    assert (response.status_code, response.json()["detail"]["reason"]) == (status, reason)


def jobs_of(songenv: SongEnv, owner: UUID) -> int:
    return songenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner).scalar_one()


def messages(songenv: SongEnv, class_id: UUID) -> list[Any]:
    return songenv.sql(
        "SELECT * FROM class_messages WHERE class_id = :c ORDER BY seq", c=class_id
    ).all()


async def test_changing_one_song_gives_a_new_playlist_and_a_revised_draft(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    class_id, base, first_list = await picked(songenv, rider)
    [old] = songenv.class_playlists(rider.user_id)
    old_entries = list(songenv.fake.playlists[old.playlist_id].entries)
    job_id, list_id, digest = await changed_waiting(songenv, rider, class_id, base)

    # Phase 1: the other songs keep their slots and roles; only slot 2 is new.
    stored = songenv.revision(list_id)
    songs = stored.content["songs"]
    assert [s["spotify_id"] for s in songs] == CHANGED
    assert [s["new"] for s in songs] == [False, False, True, False, False, False, False, False]
    assert [s["role"] for s in songs][:2] == ["warmup", "build"]
    assert (stored.parent_id, stored.provenance["origin"]) == (first_list, "revise_songs")
    assert songenv.row(job_id).credit_state == "none"  # no credit: a class is one credit
    [asked] = [m for m in messages(songenv, class_id) if m.job_id == job_id]
    assert (asked.role, asked.body, asked.base_revision_id) == ("user", REQUEST, base)

    # Phase 2 after one Approve: a new playlist of the full list, then the revised draft.
    assert (await rider.approve(job_id, list_id, digest)).status_code == 200
    songenv.script = revise_script()
    searches_before = sum(1 for s in songenv.api() if s.path == "/v1/search")
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    assert songenv.stages(job_id)[-6:] == ["prepare", "create", "fill", "attach", "music", "revise"]
    old_now, new = songenv.class_playlists(rider.user_id)
    assert songenv.fake.playlists[new.playlist_id].entries == [
        f"spotify:track:{i}" for i in CHANGED
    ]
    assert (new.revision_id, new.state, new.mowl_playlist_id is not None) == (
        list_id, "verified", True,
    )  # fmt: skip
    # The earlier playlist and its row are history, untouched.
    assert (old_now.id, old_now.state, old_now.detached_at) == (old.id, "verified", None)
    assert songenv.fake.playlists[old.playlist_id].entries == old_entries
    # No Spotify request for the kept songs: phase 2 searched nothing and read no track.
    assert sum(1 for s in songenv.api() if s.path == "/v1/search") == searches_before
    assert not any(s.path.startswith("/v1/tracks") for s in songenv.api())

    shown = songenv.sql("SELECT * FROM classes WHERE id = :id", id=class_id).one()
    draft = songenv.revision(shown.current_revision_id)
    assert (draft.kind, draft.parent_id, draft.job_id) == ("draft", base, job_id)
    assert draft.music["song_list_id"] == str(list_id)
    assert draft.music["fingerprint"] != songenv.revision(base).music["fingerprint"]
    assert draft.provenance["origin"] == "revise_songs"
    assert draft.provenance["request_message_id"] == str(asked.id)
    assert shown.music_source == {
        "source": "mowl_spotify_playlist",
        "spotify_playlist_id": new.playlist_id,
        "origin": "song_pick",
        "class_playlist_id": str(new.id),
    }
    reply = messages(songenv, class_id)[-1]
    assert (reply.role, reply.result_revision_id, reply.job_id) == ("assistant", draft.id, job_id)
    # Usage per job: each stage's agent run is the job's own.
    stages = songenv.sql("SELECT stage FROM agent_runs WHERE job_id = :id", id=job_id).all()
    assert sorted(s.stage for s in stages) == ["choose_songs", "pick_pool", "revise"]
    # The song change's pick is the songs group, its revise the design group (one each).
    models = design.current_runtime().models  # pyright: ignore[reportOptionalMemberAccess]
    assert isinstance(models, FactoryModels)
    assert models.asked[-2:] == [("pro", "songs"), ("pro", "design")]
    assert songenv.row(job_id).credit_state == "none"
    # The model was told the songs changed and never sent an id.
    sent = json.dumps([[str(t) for t in p.sent] for p in songenv.providers[-1:]])
    assert "The songs were just changed for this request" in sent
    for value in [NEW, new.playlist_id, "spotify:track"]:
        assert value not in sent


async def test_all_replaces_every_song(songenv: SongEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    songenv.sql(
        "UPDATE classes SET brief = jsonb_set(brief, '{text}', '\"Indie rock only.\"') "
        "WHERE id = :id",
        id=class_id,
    )
    seen: list[Any] = []
    build = pick.PickPlan.build

    def recording(*args: Any, **kwargs: Any) -> Any:
        seen.append(build(*args, **kwargs))
        return seen[-1]

    monkeypatch.setattr(pick.PickPlan, "build", recording)
    answer = await change(rider, class_id, base, replace="all")
    assert answer.status_code == 201, answer.text
    await songenv.run(UUID(answer.json()["id"]))  # the scripted choice does not matter here
    [plan] = seen
    assert (plan.mode, len(plan.replace), plan.kept) == ("replace", 8, ())
    # The request leads the brief's own free text.
    assert plan.brief.text == f"{REQUEST}\n\nIndie rock only."


async def test_a_class_on_its_own_playlist_is_not_song_picked(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    songenv.sql("UPDATE class_revisions SET music = music - 'song_list_id' WHERE id = :id", id=base)
    before, counts = jobs_of(songenv, rider.user_id), songenv.counts()
    refused(await change(rider, class_id, base), "not_song_picked")
    assert (jobs_of(songenv, rider.user_id), songenv.counts()) == (before, counts)
    assert [m for m in messages(songenv, class_id) if m.role == "user"] == []


async def test_the_base_rules_and_the_one_open_job(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, list_id = await picked(songenv, rider)
    refused(await change(rider, class_id, list_id), "invalid_input")  # a song list is no draft
    refused(await change(rider, class_id, uuid.uuid4()), "invalid_input")
    refused(await change(rider, class_id, base, replace=[8]), "invalid_input")  # no slot 8
    refused(await change(rider, class_id, base, replace=[]), "invalid_input", 422)
    other = await songenv.rider(spotify_user="rider-two")
    refused(await change(other, class_id, base), "class_not_found", 404)  # another user's class
    await changed_waiting(songenv, rider, class_id, base)
    refused(await change(rider, class_id, base), "class_busy")  # one open job per class


async def test_the_revision_limit_counts_song_changes(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)

    class One:
        async def limit_for(self, db: Any, ctx: Any, class_id: UUID) -> int:
            return 1

    design_limits.configure(policy=One())
    try:
        job_id, _ = await changed_approved(songenv, rider, class_id, base)
        assert (await songenv.run(job_id)).state == "completed"
        refused(await change(rider, class_id, current(songenv, class_id)), "revision_limit")
    finally:
        design_limits.configure()


async def test_an_expired_list_changes_nothing(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    job_id, list_id, digest = await changed_waiting(songenv, rider, class_id, base)
    playlists = songenv.class_playlists(rider.user_id)
    songenv.sql("UPDATE jobs SET expires_at = now() - interval '1 second' WHERE id = :i", i=job_id)
    refused(await rider.approve(job_id, list_id, digest), "expired")
    assert songenv.sql("SELECT public.queue_expire(10)").scalar_one() == 1
    assert current(songenv, class_id) == base
    assert songenv.class_playlists(rider.user_id) == playlists
    # Never approved: no receipt.
    refused(await resume_change(rider, class_id, base, list_id), "receipt_invalid")


async def test_a_resume_after_a_failure_reuses_the_playlist_and_designs(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    job_id, list_id = await changed_approved(songenv, rider, class_id, base)
    songenv.script = failing_script("auth")  # model_config after the playlist is verified
    assert (await songenv.run(job_id)).error_category == "model_config"
    playlists = songenv.class_playlists(rider.user_id)
    assert [p.state for p in playlists] == ["verified", "verified"]
    # A resume changes nothing: the input model refuses a replace or a request, even valid ones
    # (FE3's first shape sent empty ones).
    for extra in [{"replace": [1]}, {"request": "Harder songs"}, {"replace": "all"}]:
        sent = await resume_change(rider, class_id, base, list_id, **extra)
        assert sent.status_code == 422, extra
    # A design_with_songs resume cannot take a revise_songs list.
    refused(await rider.resume(class_id, list_id), "receipt_invalid")
    # The list was made to change ``base``: a resume from another current draft is stale.
    other = uuid.uuid4()
    songenv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance, parent_id) SELECT :id, owner_id, class_id, 90, kind, "
        "content, content_hash, music, provenance, id FROM class_revisions WHERE id = :base",
        id=other, base=base,
    )  # fmt: skip
    songenv.sql("UPDATE classes SET current_revision_id = :o WHERE id = :c", o=other, c=class_id)
    refused(await resume_change(rider, class_id, other, list_id), "stale_base")
    songenv.sql("UPDATE classes SET current_revision_id = :b WHERE id = :c", b=base, c=class_id)
    songenv.script = revise_script()
    writes = songenv.writes()
    answer = await resume_change(rider, class_id, base, list_id)
    assert answer.status_code == 201, answer.text
    resumed = UUID(answer.json()["id"])
    row = await songenv.run(resumed)
    assert (row.state, row.error_category) == ("completed", None)
    assert songenv.writes() == writes  # the verified playlist was reused
    after = songenv.class_playlists(rider.user_id)
    assert [(p.id, p.playlist_id, p.state) for p in after] == [
        (p.id, p.playlist_id, p.state) for p in playlists
    ]
    draft = songenv.revision(current(songenv, class_id))
    asked = songenv.revision(list_id)  # the request is the approved job's, no new message
    assert draft.music["song_list_id"] == str(asked.id)
    assert [m.role for m in messages(songenv, class_id)].count("user") == 1


async def test_a_seed_from_a_song_change_is_refused(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    job_id, list_id, _ = await changed_waiting(songenv, rider, class_id, base)
    await rider.browser.post(f"/api/jobs/{job_id}/cancel")
    seed = {"song_list_revision_id": str(list_id), "replace": [1]}
    songenv.script = design_script()
    refused(await rider.create(class_id=str(class_id), seed=seed), "invalid_input")


async def test_phase_two_stops_at_prepare_while_another_final_write_is_undecided(
    songenv: SongEnv,
) -> None:
    """#198 item 18: ``prepare`` re-checks the unresolved-write rule behind ``admit``."""
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    job_id, _ = await changed_approved(songenv, rider, class_id, base)
    songenv.sql(
        "UPDATE external_operations SET state = 'uncertain' WHERE owner_id = :o "
        "AND kind = 'spotify_replace_items' AND class_id = :c",
        o=rider.user_id, c=class_id,
    )  # fmt: skip
    writes = songenv.writes()
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    assert songenv.stages(job_id)[-1] == "prepare"
    assert songenv.writes() == writes


async def test_the_class_shows_its_stored_brief_to_its_owner_only(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, _, _ = await songenv.waiting(rider)
    class_id = songenv.row(job_id).class_id
    shown = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert shown["brief"]["length_min"] == 30  # before a design: the pick's own brief
    assert shown["brief"]["climbs"] == 1
    other = await songenv.rider(spotify_user="rider-two")
    assert (await other.browser.get(f"/api/classes/{class_id}")).status_code == 404
    songenv.sql(
        "UPDATE classes SET brief = CAST(:b AS jsonb) WHERE id = :id",
        b=json.dumps({"length_min": 45, "intensity": "hard"}), id=class_id,
    )  # fmt: skip
    shown = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert (shown["brief"]["length_min"], shown["brief"]["intensity"]) == (45, "hard")


def test_the_revise_prompt_points_other_songs_to_change_songs() -> None:
    assert '"Change songs" on the class page' in SYSTEM_PROMPT
    assert "change nothing for the songs" in SYSTEM_PROMPT
    assert "call songs_requested instead of submit_draft" in SYSTEM_PROMPT
    head = model_input.SONGS_CHANGED
    assert head.startswith("The songs were just changed")


def new_current_draft(songenv: SongEnv, class_id: UUID, base: UUID) -> UUID:
    """Another draft of the class (a copy of ``base``) made current, as a revise would."""
    other = uuid.uuid4()
    songenv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance, parent_id) SELECT :id, owner_id, class_id, 90, kind, "
        "content, content_hash, music, provenance, id FROM class_revisions WHERE id = :base",
        id=other, base=base,
    )  # fmt: skip
    songenv.sql("UPDATE classes SET current_revision_id = :o WHERE id = :c", o=other, c=class_id)
    return other


async def test_a_pick_from_a_draft_that_is_not_current_is_stale_base(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    new_current_draft(songenv, class_id, base)
    jobs, counts, said = (
        jobs_of(songenv, rider.user_id),
        songenv.counts(),
        messages(songenv, class_id),
    )
    refused(await change(rider, class_id, base), "stale_base")
    assert (jobs_of(songenv, rider.user_id), songenv.counts()) == (jobs, counts)
    assert messages(songenv, class_id) == said


async def test_phase_two_stops_at_prepare_when_the_base_is_no_longer_current(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    job_id, _ = await changed_approved(songenv, rider, class_id, base)
    other = new_current_draft(songenv, class_id, base)
    writes, playlists = songenv.writes(), len(songenv.class_playlists(rider.user_id))
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert songenv.stages(job_id)[-1] == "prepare"
    assert (songenv.writes(), len(songenv.class_playlists(rider.user_id))) == (writes, playlists)
    assert current(songenv, class_id) == other


def make_uncertain(songenv: SongEnv, owner: UUID, class_id: UUID) -> None:
    """The class's final fill marked ``uncertain`` (as an undecided write is)."""
    songenv.sql(
        "UPDATE external_operations SET state = 'uncertain' WHERE owner_id = :o "
        "AND kind = 'spotify_replace_items' AND class_id = :c",
        o=owner, c=class_id,
    )  # fmt: skip


async def test_a_pick_waits_for_an_undecided_playlist_write(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    make_uncertain(songenv, rider.user_id, class_id)
    jobs, counts, said = (
        jobs_of(songenv, rider.user_id),
        songenv.counts(),
        messages(songenv, class_id),
    )
    refused(await change(rider, class_id, base), "write_unresolved")
    assert (jobs_of(songenv, rider.user_id), songenv.counts()) == (jobs, counts)
    assert messages(songenv, class_id) == said


async def test_a_resume_waits_for_an_undecided_playlist_write(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    job_id, list_id = await changed_approved(songenv, rider, class_id, base)
    songenv.script = failing_script("auth")
    assert (await songenv.run(job_id)).error_category == "model_config"
    make_uncertain(songenv, rider.user_id, class_id)
    jobs, counts = jobs_of(songenv, rider.user_id), songenv.counts()
    refused(await resume_change(rider, class_id, base, list_id), "write_unresolved")
    assert (jobs_of(songenv, rider.user_id), songenv.counts()) == (jobs, counts)
