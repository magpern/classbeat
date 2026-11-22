"""SR C5 (ADR 0036 sections 1 and 4): a studio class designed and revised on its approved list, on
the fakes (Spotify, the SwedeBeat gateway, ``FakeProvider``), with no MOWL connection.

- the design (``studio_design``, started by its route) analyses the list, designs the workout and
  the mix, and stores a draft whose ``music`` is ``StudioRevisionMusic`` and whose hash covers the
  mix spec; it takes **no credit** (the pick's is committed) and no reference reaches the model;
- a chat revise (``studio_revise``) recomposes from the cache with **no download**; a cut alone
  makes a new draft with a new hash; the same draft and mix are no revision;
- a new pick on the approved class analyses only its new references.
"""

import asyncio
import copy
import hashlib
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import httpx
import pytest

from agentfake import good_draft
from classbeat import connections
from classbeat.design import revision, track_spotdown
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.spotdown import studio_tracks_config
from classbeat.spotdown.studio_tracks_config import TracksConfig
from classbeat.swedebeat_studio.design_kind import STUDIO_DESIGN
from classbeat.swedebeat_studio.revise_kind import STUDIO_REVISE
from ctx import make_context
from songtools import new_key
from spotdownfake import FakeSpotdown, spotdown
from studiomaps import track_map
from studiotools import StudioEnv, StudioRider, pick_script
from test_evals_studio_spotdown import document
from test_swedebeat_studio_songs import ALL_POOL, _sent  # pyright: ignore[reportPrivateUsage]

__all__ = ["spotdown"]  # the fixture
pytestmark = pytest.mark.usefixtures("analysis_slot_by_default")
LENGTH = 240.0
MIX_8 = {
    "tracks": [{"track": n, "start_sec": 0, "end_sec": LENGTH} for n in (1, 0, 2, 3, 4, 5, 6, 7)],
    "crossfades": [8] * 7,
}
# MIX_8 as whole songs, every crossfade 0: the only form a SwedeBeat playlist without DJ mix may
# bind (ADR 0036 amendment B9, ``mix_not_chosen``).
WHOLE_8 = {**MIX_8, "crossfades": [0] * 7}


def design_script(mix: dict[str, Any], *, note: str | None = None) -> dict[str, Any]:
    submit: dict[str, Any] = {"draft": {"$draft": "good"}, "mix": mix}
    if note is not None:
        submit["note"] = note
    return {
        "model": "fake-model",
        "drafts": {"good": good_draft()},
        "turns": [
            {"calls": [{"name": "get_music", "input": {}, "expect": {"ok": True}}]},
            {"calls": [{"name": "preview_draft", "input": {"draft": {"$draft": "good"}, "mix": mix},
                        "expect": {"ok": True}}]},
            {"calls": [{"name": "submit_draft", "input": submit}]},
        ],
    }  # fmt: skip


@pytest.fixture(autouse=True)
def analysed(spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """spotdown stood in by the fake: its document of each uploaded track is the 240 s map as
    analysis-v1 (the download from the fake gateway stays real). The list holds the id of every
    document the fake served (one per analysis the design asked for)."""
    seen: list[str] = []
    real = spotdown.analysis

    def analysis(analysis_id: str) -> dict[str, Any]:
        seen.append(analysis_id)
        sha = real(analysis_id)["provenance"]["audio"]["sha256"]
        made = document(track_map(LENGTH), 0)
        made["analysis_id"] = analysis_id
        made["provenance"]["audio"]["sha256"] = sha
        return made

    async def pause(*_args: object) -> None:
        return None

    monkeypatch.setattr(spotdown, "analysis", analysis)
    monkeypatch.setattr(track_spotdown, "pause", pause)
    studio_tracks_config.configure(TracksConfig(b"k" * 32))
    yield seen
    studio_tracks_config.configure(None)


async def run_kind(studio: StudioEnv, job_id: UUID) -> Any:
    """Claim and run the job until it is no longer queued (a requeue for an analysis slot)."""
    songs = studio.songs
    registry = KindRegistry()
    registry.register(STUDIO_DESIGN)
    registry.register(STUDIO_REVISE)
    for _ in range(12):
        songs.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)
        lease = songs.spot.env.settings.job_lease_seconds
        row = await queue.claim(songs.factory, "studio-w", lease, list(registry.names()))
        assert row is not None, "nothing to claim"
        await execute(
            Execution(row),
            songs.factory,
            registry,
            RunConfig(lease_seconds=lease, spotify_calls_per_job=songs.calls_per_job),
            asyncio.Event(),
        )
        if songs.row(job_id).state != "queued":
            break
    return songs.row(job_id)


async def approved(studio: StudioEnv) -> tuple[StudioRider, UUID, UUID]:
    studio.found(*ALL_POOL)
    rider = await studio.rider()
    job_id, list_id, digest = await studio.waiting(rider)
    answer = await rider.browser.approve(job_id, list_id, digest)
    assert answer.status_code == 200, answer.text
    assert (await studio.run(job_id)).state == "completed"
    return rider, studio.songs.row(job_id).class_id, list_id


async def start_design(
    rider: StudioRider, class_id: UUID, list_id: UUID, key: str | None = None
) -> httpx.Response:
    path = f"/api/classes/{class_id}/studio-songs/{list_id}/design"
    return await rider.browser.post(path, {}, {"Idempotency-Key": key or new_key()})


def downloads(studio: StudioEnv) -> list[str]:
    return [r.path for r in studio.gateway.requests if r.path.startswith("/tracks/")]


def held_credits(studio: StudioEnv, owner: UUID) -> list[str]:
    rows = studio.songs.sql(
        "SELECT kind, credit_state FROM jobs WHERE owner_id = :o ORDER BY created_at", o=owner
    ).all()
    return [f"{r.kind}:{r.credit_state}" for r in rows]


def ledger_rows(studio: StudioEnv, owner: UUID) -> int:
    """The user's credit ledger rows that count (``quota.ledger``'s held), whatever kind."""
    return studio.songs.sql(
        "SELECT count(*) FROM credit_ledger WHERE owner_id = :o "
        "AND (state = 'committed' OR (state = 'reserved' AND job_id IS NOT NULL))",
        o=owner,
    ).scalar_one()


async def designed(
    studio: StudioEnv, mix: dict[str, Any] = MIX_8
) -> tuple[StudioRider, UUID, UUID, Any]:
    rider, class_id, list_id = await approved(studio)
    studio.songs.script = design_script(mix)
    response = await start_design(rider, class_id, list_id)
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "studio_design"
    row = await run_kind(studio, UUID(response.json()["job_id"]))
    assert (row.state, row.error_category) == ("completed", None)
    current = studio.songs.sql(
        "SELECT current_revision_id, music_source, brief FROM classes WHERE id = :c", c=class_id
    ).one()
    return rider, class_id, list_id, current


async def test_a_studio_design_stores_the_mix_spec_under_its_hash_with_no_credit(
    studio: StudioEnv, analysed: list[str]
) -> None:
    rider, class_id, list_id, current = await designed(studio)
    stored = studio.songs.revision(current.current_revision_id)
    music = stored.music
    assert music["source"] == "swedebeat_studio"
    assert music["song_list_id"] == str(list_id)
    spec = music["mix_spec"]
    listed = studio.songs.revision(list_id).content["songs"]
    references = [s["swedebeat"]["reference"] for s in listed]
    assert [t["reference"] for t in spec["tracks"]] == [
        references[n] for n in (1, 0, 2, 3, 4, 5, 6, 7)
    ]
    assert spec["crossfades"] == [8] * 7
    assert music["map_version"] >= 13
    # The hash covers {content, mix_spec}, not content alone (ADR 0036 section 1).
    assert bytes(stored.content_hash) == revision.studio_hash(stored.content, spec)
    assert bytes(stored.content_hash) != hashlib.sha256(revision.canonical(stored.content)).digest()
    # The class adopted the brief and the studio source; one credit, the pick's.
    assert current.music_source == {"source": "swedebeat_studio"}
    assert current.brief is not None
    assert held_credits(studio, rider.user_id) == ["studio_songs:reserved", "studio_design:none"]
    assert ledger_rows(studio, rider.user_id) == 1  # the pick's, settled with its own job
    # Each track downloaded once and sent to spotdown; the fake gateway serves the same bytes for
    # every track, so spotdown holds one analysis of them. No reference reached the model.
    assert len(downloads(studio)) == 8
    assert len(analysed) == 1
    sent = _sent(studio)
    for reference in references:
        assert reference not in sent
    # A second design of the same list is refused; nothing more is analysed.
    again = await start_design(rider, class_id, list_id)
    assert again.status_code == 409
    assert again.json()["detail"]["reason"] == "already_designed"


async def test_the_route_is_idempotent_and_answers_another_users_list_as_not_found(
    studio: StudioEnv,
) -> None:
    rider, class_id, list_id = await approved(studio)
    key = new_key()
    first = await start_design(rider, class_id, list_id, key)
    second = await start_design(rider, class_id, list_id, key)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["job_id"] == second.json()["job_id"]
    other = await studio.rider()
    foreign = await start_design(other, class_id, list_id)
    assert foreign.status_code == 404
    missing = await rider.browser.post(f"/api/classes/{class_id}/studio-songs/{list_id}/design", {})
    assert missing.status_code == 400  # no Idempotency-Key


async def test_a_list_still_waiting_for_approval_cannot_be_designed(studio: StudioEnv) -> None:
    studio.found(*ALL_POOL)
    rider = await studio.rider()
    job_id, list_id, _ = await studio.waiting(rider)
    class_id = studio.songs.row(job_id).class_id
    refused = await start_design(rider, class_id, list_id)
    assert refused.status_code in (409, 422)  # the class is busy, or the list is not approved
    assert downloads(studio) == []


async def revise(rider: StudioRider, class_id: UUID, base: UUID, request: str) -> Any:
    body = {
        "kind": "studio_revise",
        "input": {
            "class_id": str(class_id),
            "base_revision_id": str(base),
            "request": request,
            "swedebeat_connection_id": str(rider.swedebeat_connection_id),
        },
    }
    return await rider.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


async def test_a_revise_recomposes_with_no_download_and_a_cut_makes_a_new_hash(
    studio: StudioEnv, analysed: list[str]
) -> None:
    rider, class_id, _, current = await designed(studio)
    base = studio.songs.revision(current.current_revision_id)
    fetched, seen = downloads(studio), list(analysed)
    cut = copy.deepcopy(MIX_8)
    cut["tracks"][-1]["end_sec"] = 230.0  # after the workout's end: the fitted draft is the same
    studio.songs.script = design_script(cut, note="Cut the last song.")
    response = await revise(rider, class_id, base.id, "Cut the last song shorter")
    assert response.status_code == 201, response.text
    row = await run_kind(studio, UUID(response.json()["id"]))
    assert (row.state, row.error_category) == ("completed", None)
    assert downloads(studio) == fetched  # no download, no match
    assert analysed == seen
    new_id = studio.songs.sql(
        "SELECT current_revision_id FROM classes WHERE id = :c", c=class_id
    ).scalar_one()
    new = studio.songs.revision(new_id)
    assert new.content == base.content  # the same workout: only the cut changed
    assert new.music["mix_spec"] != base.music["mix_spec"]
    assert bytes(new.content_hash) != bytes(base.content_hash)
    assert new.parent_id == base.id
    assert new.provenance["origin"] == "revise_draft"
    last = new.music["mix_spec"]["tracks"][-1]
    assert abs(last["end_sec"] - 230.0) <= 240.0 / 128.0 / 2  # on a downbeat near 230 s
    # The same draft and mix again change nothing: not a revision.
    studio.songs.script = design_script(cut)
    again = await revise(rider, class_id, new_id, "Do it again")
    row = await run_kind(studio, UUID(again.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "invalid_output")
    assert held_credits(studio, rider.user_id).count("studio_revise:none") == 2


async def test_a_revise_whose_analysis_is_gone_downloads_nothing(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio)
    fetched = downloads(studio)
    studio.songs.sql("DELETE FROM swedebeat_track_maps WHERE owner_id = :o", o=rider.user_id)
    studio.songs.script = design_script(MIX_8)
    response = await revise(rider, class_id, current.current_revision_id, "Harder")
    row = await run_kind(studio, UUID(response.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "music_changed")
    assert downloads(studio) == fetched


async def test_a_revise_draft_is_refused_on_a_studio_class_by_its_music_kind(
    studio: StudioEnv,
) -> None:
    """With the user's real MOWL connection, so nothing but the class's music kind refuses it."""
    rider, class_id, _, current = await designed(studio)
    mowl_id = await connections.save(
        studio.songs.factory, studio.songs.ring, make_context(rider.user_id), "mowl",
        {"token": "invented-mowl-token-0001"},
    )  # fmt: skip
    body = {
        "kind": "revise_draft",
        "input": {
            "class_id": str(class_id),
            "base_revision_id": str(current.current_revision_id),
            "request": "Harder",
            "mowl_connection_id": str(mowl_id),
        },
    }
    refused = await rider.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "invalid_input"
    jobs = studio.songs.sql(
        "SELECT count(*) FROM jobs WHERE owner_id = :o AND kind = 'revise_draft'", o=rider.user_id
    ).scalar_one()
    assert jobs == 0


async def test_a_new_pick_on_a_designed_class_analyses_only_new_references(
    studio: StudioEnv, analysed: list[str]
) -> None:
    rider, class_id, first_list, current = await designed(studio)
    fetched = downloads(studio)
    studio.songs.script = pick_script()  # the same songs again: every reference is cached
    job_id, list_id, digest = await studio.waiting(rider, class_id=str(class_id))
    assert list_id != first_list
    assert (await rider.browser.approve(job_id, list_id, digest)).status_code == 200
    assert (await studio.run(job_id)).state == "completed"
    studio.songs.script = design_script(MIX_8)
    response = await start_design(rider, class_id, list_id)
    assert response.status_code == 201, response.text
    row = await run_kind(studio, UUID(response.json()["job_id"]))
    assert (row.state, row.error_category) == ("completed", None)
    assert downloads(studio) == fetched  # nothing new to download
    now = studio.songs.sql(
        "SELECT current_revision_id FROM classes WHERE id = :c", c=class_id
    ).scalar_one()
    assert now != current.current_revision_id  # the design on the newer list is current
    assert studio.songs.revision(now).music["song_list_id"] == str(list_id)
    # The older list is no longer the newest: it cannot be designed again.
    stale = await start_design(rider, class_id, first_list)
    assert stale.status_code == 409
    assert stale.json()["detail"]["reason"] == "stale_base"


async def test_a_browser_cannot_send_the_brief_of_a_studio_design(studio: StudioEnv) -> None:
    rider, class_id, list_id = await approved(studio)
    body = {
        "kind": "studio_design",
        "input": {
            "class_id": str(class_id),
            "song_list_revision_id": str(list_id),
            "swedebeat_connection_id": str(rider.swedebeat_connection_id),
            "brief": {"length_min": 90, "intensity": "hard"},
        },
    }
    refused = await rider.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert refused.status_code == 422, refused.text
    assert downloads(studio) == []


async def test_a_list_whose_pick_ended_without_approval_cannot_be_designed(
    studio: StudioEnv,
) -> None:
    """The list is the class's newest and has no gap, but its ``studio_songs`` job was cancelled
    while it waited: only the pick's approval makes a list designable (``design_admit``)."""
    studio.found(*ALL_POOL)
    rider = await studio.rider()
    job_id, list_id, _ = await studio.waiting(rider)
    class_id = studio.songs.row(job_id).class_id
    cancelled = await rider.browser.post(f"/api/jobs/{job_id}/cancel", {})
    assert cancelled.status_code == 200, cancelled.text
    assert studio.songs.row(job_id).state == "cancelled"
    refused = await start_design(rider, class_id, list_id)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["reason"] == "invalid_input"
    assert downloads(studio) == []


async def test_a_revise_with_one_analysis_gone_is_music_changed(studio: StudioEnv) -> None:
    rider, class_id, list_id, current = await designed(studio)
    fetched = downloads(studio)
    listed = studio.songs.revision(list_id).content["songs"]
    one = listed[3]["swedebeat"]["reference"]
    studio.songs.sql(
        "DELETE FROM swedebeat_track_maps WHERE owner_id = :o AND reference = :r",
        o=rider.user_id,
        r=one,
    )
    studio.songs.script = design_script(MIX_8)
    response = await revise(rider, class_id, current.current_revision_id, "Harder")
    row = await run_kind(studio, UUID(response.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "music_changed")
    assert downloads(studio) == fetched


async def test_a_studio_class_is_not_duplicated(studio: StudioEnv) -> None:
    """A studio draft's hash covers its mix spec, so the content-only copy is refused by name
    (SR C5b), never a 500, and nothing is written."""
    rider, class_id, _, current = await designed(studio)
    stored = studio.songs.revision(current.current_revision_id)
    body = {
        "revision_id": str(stored.id),
        "content_hash": bytes(stored.content_hash).hex(),
        "title": "Copy",
    }
    path = f"/api/classes/{class_id}/duplicate"
    refused = await rider.browser.post(path, body, {"Idempotency-Key": new_key()})
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "studio_not_duplicable"
    classes = studio.songs.sql(
        "SELECT count(*) FROM classes WHERE owner_id = :o", o=rider.user_id
    ).scalar_one()
    assert classes == 1
