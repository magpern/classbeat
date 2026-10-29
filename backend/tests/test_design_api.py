"""The web side of ``design_draft`` (D1): the kind in the jobs API's registry (J3), a job created
through ``POST /api/jobs`` and run by the worker side, and ``GET /api/music/mowl-playlists``
(names only) against the fake MOWL. Never the network."""

import json
import subprocess
import sys
from typing import Any
from uuid import UUID

import pytest

from classbeat.agent import WorkoutDraft
from classbeat.design import DESIGN_DRAFT, KIND
from classbeat.jobs import KINDS
from designtools import BRIEF, PLAYLIST_NAME, DesignEnv, FakeMowlMusic
from jobapi import Browser
from optools import PLAYLIST, TOKEN_A, TOKEN_B

pytestmark = pytest.mark.usefixtures("jenv")

UNKNOWN_TOKEN = "cccccccc-0000-1111-2222-333333333333"  # a public test value


def test_web_and_worker_both_register_the_kind() -> None:
    assert KINDS.get(KIND) is DESIGN_DRAFT
    for entry in ("classbeat.web", "classbeat.worker.__main__"):
        code = (
            f"import {entry}; from classbeat.jobs import KINDS; "
            "import sys; sys.exit(0 if KINDS.get('design_draft') else 1)"
        )
        done = subprocess.run([sys.executable, "-c", code], check=False)  # noqa: S603 - constant
        assert done.returncode == 0, entry


def body(browser: Browser, source_id: str = PLAYLIST) -> dict[str, Any]:
    return {
        "kind": KIND,
        "input": {
            "title": "Tuesday climb",
            "brief": BRIEF,
            "music": {"source": "mowl_spotify_playlist", "spotify_playlist_id": source_id},
            "mowl_connection_id": str(browser.connection_id),
        },
    }


async def test_a_job_created_through_the_api_is_designed_by_the_worker(
    browsers: Any, denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    browser: Browser = await browsers()
    denv.app.app.dependency_overrides.clear()  # the real registry, as in production
    await denv.ops.connect(browser.user_id, TOKEN_A)  # a reconnect in place: the same id
    key = {"Idempotency-Key": "d1-design-draft-key-0001"}
    created = await browser.post("/api/jobs", body(browser), key)
    assert created.status_code == 201, created.text
    job_id = UUID(created.json()["id"])
    await denv.run_next()
    shown = await browser.get(f"/api/jobs/{job_id}")
    assert (shown.json()["state"], shown.json()["error_category"]) == ("completed", None)
    assert len(denv.revisions(job_id)) == 1

    # U1: the draft the worker stored (through design.revision.build) reads back through the
    # web API as the same WorkoutDraft, with only the allowed keys.
    [stored] = denv.revisions(job_id)
    read = await browser.get(f"/api/jobs/{job_id}/revision")
    assert read.status_code == 200, read.text
    answer = read.json()
    assert set(answer) == {"revision_id", "number", "kind", "content", "unknown_bpm", "created_at"}
    assert (answer["revision_id"], answer["number"], answer["kind"]) == (str(stored.id), 1, "draft")
    assert WorkoutDraft.model_validate(answer["content"]) == WorkoutDraft.model_validate(
        stored.content
    )
    assert answer["unknown_bpm"] == stored.music["unknown_bpm"]
    assert stored.music["fingerprint"] not in read.text  # no fingerprint or link id
    assert stored.music["link_id"] not in read.text


@pytest.mark.parametrize(
    "change",
    [
        {"music": {"source": "mowl_spotify_playlist", "spotify_playlist_id": "not-a-spotify-id"}},
        {"music": {"source": "swedebeat", "spotify_playlist_id": PLAYLIST}},
        {"brief": {"length_min": 5}},
        {"brief": {**BRIEF, "extra": 1}},
        {"brief": {**BRIEF, "length_min": "30"}},  # strict: no coercion
        {"unexpected": True},
    ],
)
async def test_an_invalid_design_input_is_refused_by_the_api(
    browsers: Any, change: dict[str, Any]
) -> None:
    browser: Browser = await browsers()
    browser.env.app.dependency_overrides.clear()
    sent = body(browser)
    sent["input"] |= change
    response = await browser.post("/api/jobs", sent, {"Idempotency-Key": "d1-invalid-input-0001"})
    assert response.status_code == 422, response.text
    assert "not-a-spotify-id" not in response.text


def connection_row(denv: DesignEnv, owner: UUID) -> Any:
    return denv.ops.sql(
        "SELECT status, last_error, last_used_at, updated_at FROM connections WHERE owner_id = :o",
        o=owner,
    ).one()


async def test_the_playlists_are_listed_with_ids_and_names_only(
    browsers: Any, denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    browser: Browser = await browsers(mowl=False)
    await denv.ops.connect(browser.user_id, TOKEN_A)
    fake_mowl.names[TOKEN_A] = {
        PLAYLIST: PLAYLIST_NAME,
        "4hOKQuZbraPDIfaGbM3lKI": "Ride\u202e\x00 two",
        "not a spotify id": "left out",
    }
    fake_mowl.names[TOKEN_B] = {"7xGfFoTpQ2E7fRF5lN10tr": "another account's list"}
    before = connection_row(denv, browser.user_id)
    response = await browser.get("/api/music/mowl-playlists")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "playlists": [
            {"spotify_playlist_id": PLAYLIST, "name": PLAYLIST_NAME},
            {"spotify_playlist_id": "4hOKQuZbraPDIfaGbM3lKI", "name": "Ride two"},
        ]
    }
    assert fake_mowl.reads == ["/v1/Spotify/Playlists"]
    assert fake_mowl.tokens_read == [TOKEN_A]
    assert TOKEN_A not in response.text
    assert "TrackCount" not in response.text
    assert connection_row(denv, browser.user_id) == before  # a GET writes nothing


async def test_the_playlists_need_a_signed_in_user_and_a_mowl_connection(
    browsers: Any, fake_mowl: FakeMowlMusic
) -> None:
    browser: Browser = await browsers(mowl=False)
    anonymous = await browser.client.get("/api/music/mowl-playlists")
    assert anonymous.status_code == 401
    response = await browser.get("/api/music/mowl-playlists")
    assert (response.status_code, response.json()) == (
        409,
        {"detail": {"reason": "not_connected"}},
    )
    assert fake_mowl.requests == []


async def test_a_token_mowl_refuses_answers_needs_reconnect_and_writes_nothing(
    browsers: Any, denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    """Architect decision on #119: the GET does not flip the connection (the job path does)."""
    browser: Browser = await browsers(mowl=False)
    await denv.ops.connect(browser.user_id, UNKNOWN_TOKEN)  # the fake answers it 401
    before = connection_row(denv, browser.user_id)
    response = await browser.get("/api/music/mowl-playlists")
    assert (response.status_code, response.json()) == (
        409,
        {"detail": {"reason": "needs_reconnect"}},
    )
    after = connection_row(denv, browser.user_id)
    assert after == before
    assert (after.status, after.last_error) == ("connected", None)
    assert fake_mowl.tokens_read == [UNKNOWN_TOKEN]
    assert UNKNOWN_TOKEN not in json.dumps(response.json())
