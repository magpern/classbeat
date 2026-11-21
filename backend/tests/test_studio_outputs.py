"""SR C6a (ADR 0036 sections 5 and 6; ADR 0005 section 2; ADR 0007 section 5): a designed studio
class's timeline and the user's output choice, on the fakes (Spotify, the SwedeBeat gateway,
``FakeProvider``), against Postgres as ``classbeat_app``. Never the network.

- ``GET /timeline`` is ``compose_map`` of the stored mix spec and the cached analyses, with the
  list's SwedeBeat facts and no reference;
- ``POST /outputs`` binds the draft id, its hash, the outputs and the SwedeBeat connection with its
  generation, after one ``GET /auth/session``; a stale hash, MOWL without the mix, a disabled
  output, another user's class, a refused session and a reconnect are refused with nothing
  stored (and, before the session check, nothing asked of the gateway); the ``Idempotency-Key``
  replays and refuses a different body; only this route can create the job;
- ``produce_outputs`` re-checks the binding when it starts, with nothing written when it fails:
  a job without the binding version (approved for C6a's stub), a reconnect, another Spotify
  account (``account_changed``), a draft that changed after the create, an output turned off.
  Its writes are SR C7's (``test_studio_produce_playlist``, ``test_studio_produce_mix``).
"""

import asyncio
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import httpx
import pytest
from mowl import compose

from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.swedebeat_studio import config as studio_config
from classbeat.swedebeat_studio import outputs_session
from classbeat.swedebeat_studio.outputs_kind import PRODUCE_OUTPUTS
from gatewaycore import SESSION_PATH
from songtools import new_key
from studiotools import StudioEnv, StudioRider
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    WHOLE_8,
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    designed,
)

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default")
ALL_ON = frozenset({"design", "swedebeat_playlist", "swedebeat_mix", "mowl"})


@pytest.fixture
def outputs_on(studio: StudioEnv) -> Iterator[None]:
    studio_config.configure(studio_config.StudioConfig(ALL_ON))
    yield
    studio_config.configure(studio_config.StudioConfig(frozenset({"design"})))


def sessions(studio: StudioEnv) -> int:
    return sum(1 for r in studio.gateway.requests if r.path == SESSION_PATH)


def produce_jobs(studio: StudioEnv, owner: UUID) -> list[Any]:
    return studio.songs.sql(
        "SELECT id, state, error_category, input FROM jobs WHERE owner_id = :o "
        "AND kind = 'produce_outputs' ORDER BY created_at",
        o=owner,
    ).all()


async def choose(
    rider: StudioRider, class_id: UUID, body: dict[str, Any], key: str | None = None
) -> httpx.Response:
    path = f"/api/classes/{class_id}/outputs"
    return await rider.browser.post(path, body, {"Idempotency-Key": key or new_key()})


def choice(current: Any, studio: StudioEnv, *outputs: str) -> dict[str, Any]:
    stored = studio.songs.revision(current.current_revision_id)
    return {
        "revision_id": str(current.current_revision_id),
        "content_hash": bytes(stored.content_hash).hex(),
        "outputs": list(outputs),
    }


async def run_outputs(studio: StudioEnv, job_id: UUID) -> Any:
    songs = studio.songs
    songs.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)
    lease = songs.spot.env.settings.job_lease_seconds
    registry = KindRegistry()
    registry.register(PRODUCE_OUTPUTS)
    row = await queue.claim(songs.factory, "outputs-w", lease, list(registry.names()))
    assert row is not None, "nothing to claim"
    await execute(
        Execution(row),
        songs.factory,
        registry,
        RunConfig(lease_seconds=lease, spotify_calls_per_job=songs.calls_per_job),
        asyncio.Event(),
    )
    return songs.row(job_id)


def outputs_of(studio: StudioEnv, owner: UUID) -> int:
    return studio.songs.sql(
        "SELECT count(*) FROM class_outputs WHERE owner_id = :o", o=owner
    ).scalar_one()


def no_writes(studio: StudioEnv) -> None:
    """Nothing but the session check and the design's own calls reached the gateway."""
    allowed = {SESSION_PATH, "/auth/login", "/catalog/match"}
    for r in studio.gateway.requests:
        assert r.path in allowed or r.path.startswith("/tracks/"), r.path
        assert r.method == "GET" or r.path != SESSION_PATH


# --- The timeline -------------------------------------------------------------------------------


async def test_the_timeline_is_the_composed_map_of_the_stored_spec(studio: StudioEnv) -> None:
    rider, class_id, list_id, current = await designed(studio)
    answer = await rider.browser.get(f"/api/classes/{class_id}/timeline")
    assert answer.status_code == 200, answer.text
    view = answer.json()
    stored = studio.songs.revision(current.current_revision_id)
    spec = stored.music["mix_spec"]
    assert view["revision_id"] == str(current.current_revision_id)
    assert view["content_hash"] == bytes(stored.content_hash).hex()
    references = [t["reference"] for t in spec["tracks"]]
    maps = {
        r.reference: r.map
        for r in studio.songs.sql(
            "SELECT reference, map FROM swedebeat_track_maps WHERE owner_id = :o",
            o=rider.user_id,
        ).all()
    }
    composed = compose.compose_map([maps[r] for r in references], spec)
    assert view["duration_sec"] == composed["duration"]
    assert [(t["mix_start_sec"], t["mix_end_sec"]) for t in view["tracks"]] == [
        (s["start"], s["end"]) for s in composed["songs"]
    ]
    assert [c["sec"] for c in view["crossfades"]] == spec["crossfades"]
    assert sum(t["effective_sec"] for t in view["tracks"]) == pytest.approx(view["duration_sec"])
    # The songs by the list's SwedeBeat facts, in the mix's order; no reference, no Spotify id.
    listed = {
        s["swedebeat"]["reference"]: s for s in studio.songs.revision(list_id).content["songs"]
    }
    assert [(t["title"], t["artist"]) for t in view["tracks"]] == [
        (listed[r]["swedebeat"]["title"], listed[r]["swedebeat"]["artist"]) for r in references
    ]
    for reference, song in listed.items():
        assert reference not in answer.text
        assert song["spotify_id"] not in answer.text
    intervals = view["intervals"]
    assert intervals[0]["start_sec"] == 0
    assert view["workout_sec"] == sum(i["sec"] for i in intervals)


async def test_the_timeline_of_another_users_class_is_not_found(studio: StudioEnv) -> None:
    _, class_id, _, _ = await designed(studio)
    other = await studio.rider()
    answer = await other.browser.get(f"/api/classes/{class_id}/timeline")
    assert (answer.status_code, answer.json()["detail"]) == (404, {"reason": "not_found"})


async def test_the_timeline_without_the_analyses_is_music_changed(studio: StudioEnv) -> None:
    rider, class_id, _, _ = await designed(studio)
    studio.songs.sql("DELETE FROM swedebeat_track_maps WHERE owner_id = :o", o=rider.user_id)
    answer = await rider.browser.get(f"/api/classes/{class_id}/timeline")
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "music_changed")


# --- The output choice --------------------------------------------------------------------------


@pytest.mark.usefixtures("outputs_on")
async def test_a_choice_binds_the_draft_the_outputs_and_the_connection(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio, WHOLE_8)
    before = sessions(studio)
    body = choice(current, studio, "swedebeat_mix", "swedebeat_playlist")
    key = new_key()
    answer = await choose(rider, class_id, body, key)
    assert answer.status_code == 201, answer.text
    assert answer.json()["kind"] == "produce_outputs"
    assert sessions(studio) == before + 1  # GET /auth/session before the choice is accepted
    (job,) = produce_jobs(studio, rider.user_id)
    generation = studio.songs.sql(
        "SELECT generation FROM connections WHERE id = :c", c=rider.swedebeat_connection_id
    ).scalar_one()
    assert job.input == {
        "class_id": str(class_id),
        "title": None,
        "revision_id": body["revision_id"],
        "content_hash": body["content_hash"],
        "outputs": ["swedebeat_playlist", "swedebeat_mix"],  # stored in one order
        "swedebeat_connection_id": str(rider.swedebeat_connection_id),
        "spotify_connection_id": None,
        "swedebeat_generation": generation,
        "binding_version": 2,
    }
    # The same key and body again (in another order): the same job, no second one.
    again = await choose(rider, class_id, {**body, "outputs": body["outputs"][::-1]}, key)
    assert (again.status_code, again.json()["job_id"]) == (200, answer.json()["job_id"])
    # The same key with another choice: refused, nothing stored.
    other = await choose(rider, class_id, {**body, "outputs": ["swedebeat_playlist"]}, key)
    assert (other.status_code, other.json()["detail"]["reason"]) == (409, "idempotency_mismatch")
    assert len(produce_jobs(studio, rider.user_id)) == 1


@pytest.mark.usefixtures("outputs_on")
@pytest.mark.parametrize("version", [None, 1, 3])
async def test_a_job_without_this_binding_version_writes_nothing(
    studio: StudioEnv, version: int | None
) -> None:
    rider, class_id, _, current = await designed(studio, WHOLE_8)
    answer = await choose(rider, class_id, choice(current, studio, "swedebeat_playlist"))
    assert answer.status_code == 201, answer.text
    job_id = UUID(answer.json()["job_id"])
    # As a job approved in the C6a era (the stub) was stored: no binding_version, or another.
    studio.songs.sql(
        "UPDATE jobs SET input = CASE WHEN CAST(:v AS int) IS NULL "
        "THEN input - 'binding_version' "
        "ELSE jsonb_set(input, '{binding_version}', to_jsonb(CAST(:v AS int))) END WHERE id = :id",
        v=version,
        id=job_id,
    )
    done = await run_outputs(studio, job_id)
    assert (done.state, done.error_category) == ("failed", "invalid_input")
    no_writes(studio)
    assert outputs_of(studio, rider.user_id) == 0


@pytest.mark.usefixtures("outputs_on")
async def test_the_spotify_playlist_binds_the_spotify_connection(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio)
    answer = await choose(rider, class_id, choice(current, studio, "spotify_playlist"))
    assert answer.status_code == 201, answer.text
    (job,) = produce_jobs(studio, rider.user_id)
    assert job.input["spotify_connection_id"] == str(rider.spotify_connection_id)


@pytest.mark.usefixtures("outputs_on")
@pytest.mark.parametrize(
    ("change", "status", "reason"),
    [
        ({"content_hash": "0" * 64}, 409, "hash_mismatch"),  # a stale hash
        ({"outputs": ["mowl"]}, 409, "mowl_needs_mix"),
        ({"outputs": ["swedebeat_playlist", "mowl"]}, 409, "mowl_needs_mix"),
        ({"outputs": []}, 422, "invalid_input"),
        ({"outputs": ["swedebeat_mix", "swedebeat_mix"]}, 422, "invalid_input"),
        ({"outputs": ["private_playlist"]}, 422, "invalid_input"),
        ({"revision_id": "00000000-0000-4000-8000-000000000000"}, 409, "not_current"),
        ({"title": "a body field of its own"}, 422, "invalid_input"),
    ],
)
async def test_a_refused_choice_stores_nothing_and_asks_the_gateway_nothing(
    studio: StudioEnv, change: dict[str, Any], status: int, reason: str
) -> None:
    rider, class_id, _, current = await designed(studio)
    before = sessions(studio)
    answer = await choose(rider, class_id, {**choice(current, studio, "swedebeat_mix"), **change})
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (status, reason)
    assert produce_jobs(studio, rider.user_id) == []
    assert sessions(studio) == before


async def test_bass_swap_is_never_an_output_even_with_every_value_on(studio: StudioEnv) -> None:
    """ADR 0036 amendment B8 (#1368): ``bass_swap`` is a setting value, never a choice."""
    rider, class_id, _, current = await designed(studio)
    studio_config.configure(studio_config.StudioConfig(ALL_ON | {"bass_swap"}))
    try:
        for chosen in (["bass_swap"], ["swedebeat_mix", "bass_swap"]):
            answer = await choose(rider, class_id, choice(current, studio, *chosen))
            assert (answer.status_code, answer.json()["detail"]["reason"]) == (422, "invalid_input")
        assert produce_jobs(studio, rider.user_id) == []
    finally:
        studio_config.configure(studio_config.StudioConfig(frozenset({"design"})))


async def test_an_output_the_setting_does_not_run_is_refused(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio, WHOLE_8)  # the studio's "design" only
    for chosen in (["swedebeat_playlist"], ["swedebeat_mix"], ["swedebeat_mix", "mowl"]):
        answer = await choose(rider, class_id, choice(current, studio, *chosen))
        assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "output_off")
    studio_config.configure(studio_config.StudioConfig(frozenset({"design", "swedebeat_mix"})))
    try:
        refused = await choose(rider, class_id, choice(current, studio, "swedebeat_playlist"))
        assert refused.json()["detail"]["reason"] == "output_off"
        accepted = await choose(rider, class_id, choice(current, studio, "swedebeat_mix"))
        assert accepted.status_code == 201, accepted.text
    finally:
        studio_config.configure(studio_config.StudioConfig(frozenset({"design"})))


@pytest.mark.usefixtures("outputs_on")
async def test_another_users_class_is_not_found(studio: StudioEnv) -> None:
    _, class_id, _, current = await designed(studio, WHOLE_8)
    other = await studio.rider()
    before = sessions(studio)
    answer = await choose(other, class_id, choice(current, studio, "swedebeat_playlist"))
    assert (answer.status_code, answer.json()["detail"]) == (404, {"reason": "not_found"})
    assert produce_jobs(studio, other.user_id) == []
    assert sessions(studio) == before


@pytest.mark.usefixtures("outputs_on")
async def test_a_refused_session_is_refused_with_nothing_stored(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio, WHOLE_8)
    studio.gateway.inject("bearer_401", SESSION_PATH)
    answer = await choose(rider, class_id, choice(current, studio, "swedebeat_playlist"))
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "session_refused")
    assert produce_jobs(studio, rider.user_id) == []
    status = studio.songs.sql(
        "SELECT status FROM connections WHERE id = :c", c=rider.swedebeat_connection_id
    ).scalar_one()
    assert status == "connected"  # the page's check flips nothing


@pytest.mark.usefixtures("outputs_on")
async def test_a_reconnect_after_the_session_check_is_connection_changed(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, _, current = await designed(studio, WHOLE_8)
    real = outputs_session.check

    async def then_reconnected(*args: Any) -> Any:
        checked = await real(*args)
        studio.songs.sql(
            "UPDATE connections SET generation = generation + 1 WHERE id = :c",
            c=rider.swedebeat_connection_id,
        )
        return checked

    monkeypatch.setattr(outputs_session, "check", then_reconnected)
    answer = await choose(rider, class_id, choice(current, studio, "swedebeat_playlist"))
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "connection_changed")
    assert produce_jobs(studio, rider.user_id) == []


@pytest.mark.usefixtures("outputs_on")
async def test_a_reconnect_before_the_job_starts_refuses_it(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio, WHOLE_8)
    answer = await choose(rider, class_id, choice(current, studio, "swedebeat_playlist"))
    assert answer.status_code == 201, answer.text
    studio.songs.sql(
        "UPDATE connections SET generation = generation + 1 WHERE id = :c",
        c=rider.swedebeat_connection_id,
    )
    done = await run_outputs(studio, UUID(answer.json()["job_id"]))
    assert (done.state, done.error_category) == ("failed", "connection_changed")
    no_writes(studio)


def other_spotify_account(studio: StudioEnv, rider: StudioRider) -> None:
    studio.songs.sql(
        "UPDATE connections SET public = jsonb_set(public, '{spotify_user_id}', "
        "'\"someone-else\"') WHERE id = :c",
        c=rider.spotify_connection_id,
    )


@pytest.mark.usefixtures("outputs_on")
async def test_another_spotify_account_is_account_changed(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio)
    other_spotify_account(studio, rider)
    answer = await choose(rider, class_id, choice(current, studio, "spotify_playlist"))
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "account_changed")
    assert produce_jobs(studio, rider.user_id) == []


@pytest.mark.usefixtures("outputs_on")
async def test_another_spotify_account_before_the_job_starts_refuses_it(
    studio: StudioEnv,
) -> None:
    rider, class_id, _, current = await designed(studio)
    before = set(studio.songs.fake.playlists)
    answer = await choose(rider, class_id, choice(current, studio, "spotify_playlist"))
    assert answer.status_code == 201, answer.text
    other_spotify_account(studio, rider)
    done = await run_outputs(studio, UUID(answer.json()["job_id"]))
    assert (done.state, done.error_category) == ("failed", "account_changed")
    assert set(studio.songs.fake.playlists) == before
    assert outputs_of(studio, rider.user_id) == 0


@pytest.mark.usefixtures("outputs_on")
async def test_a_draft_changed_after_the_create_refuses_the_job(studio: StudioEnv) -> None:
    rider, class_id, list_id, current = await designed(studio, WHOLE_8)
    answer = await choose(rider, class_id, choice(current, studio, "swedebeat_playlist"))
    assert answer.status_code == 201, answer.text
    # Another draft is now the class's current one (as a revise between the create and the run).
    studio.songs.sql(
        "UPDATE classes SET current_revision_id = :r WHERE id = :c", r=list_id, c=class_id
    )
    done = await run_outputs(studio, UUID(answer.json()["job_id"]))
    assert (done.state, done.error_category) == ("failed", "invalid_input")
    no_writes(studio)
    assert outputs_of(studio, rider.user_id) == 0


@pytest.mark.usefixtures("outputs_on")
async def test_an_output_turned_off_before_the_job_starts_refuses_it(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio)
    answer = await choose(rider, class_id, choice(current, studio, "swedebeat_mix", "mowl"))
    assert answer.status_code == 201, answer.text
    studio_config.configure(studio_config.StudioConfig(frozenset({"design", "swedebeat_mix"})))
    done = await run_outputs(studio, UUID(answer.json()["job_id"]))
    assert (done.state, done.error_category) == ("failed", "feature_off")


@pytest.mark.usefixtures("outputs_on")
async def test_only_the_consent_route_creates_the_job(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio)
    body = {
        "kind": "produce_outputs",
        "input": {
            **choice(current, studio, "swedebeat_playlist"),
            "class_id": str(class_id),
            "swedebeat_connection_id": str(rider.swedebeat_connection_id),
        },
    }
    answer = await rider.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "consent_required")
    for server_field in ("swedebeat_generation", "binding_version"):
        with_field = {**body, "input": {**body["input"], server_field: 2}}
        sent = await rider.browser.post("/api/jobs", with_field, {"Idempotency-Key": new_key()})
        assert sent.status_code == 422  # a server field
    assert produce_jobs(studio, rider.user_id) == []


@pytest.mark.usefixtures("outputs_on")
async def test_a_busy_class_asks_the_gateway_nothing(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio, WHOLE_8)
    first = await choose(rider, class_id, choice(current, studio, "swedebeat_playlist"))
    assert first.status_code == 201, first.text
    before = sessions(studio)
    second = await choose(rider, class_id, choice(current, studio, "swedebeat_mix"))
    assert (second.status_code, second.json()["detail"]["reason"]) == (409, "class_busy")
    assert sessions(studio) == before
