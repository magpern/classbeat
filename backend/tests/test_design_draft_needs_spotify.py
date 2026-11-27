"""``POST /api/jobs`` ``design_draft`` on a Spotify playlist needs the Spotify connection, as the
``interpret_request`` admit does (roadmap Amendment 25, #1145): a cycling class with no connection
is refused ``needs_spotify`` (``song_picking_unavailable`` when song picking is off for the user)
before any job or credit; with one it is admitted, kill switch or not; a hand-off of an
interpretation stored before Amendment 25 (no Spotify) still designs. Fake MOWL, no network."""

import json
from typing import Any
from uuid import UUID

import pytest

from classbeat.design import DESIGN_DRAFT, kind_music
from designtools import BRIEF, PLAYLIST
from interprettools import InterpretEnv
from jobapi import new_key

pytestmark = pytest.mark.usefixtures("jenv")


async def post_draft(ienv: InterpretEnv, music: dict[str, Any], **brief: Any) -> Any:
    body = {
        "kind": DESIGN_DRAFT.name,
        "input": {
            "title": "A class",
            "brief": BRIEF | brief,
            "music": music,
            "mowl_connection_id": str(ienv.renv.browser.connection_id),
        },
    }
    return await ienv.renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


PLAYLIST_MUSIC = {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST}


def picking(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    async def admits(*_args: Any) -> bool:
        return on

    monkeypatch.setattr(kind_music, "song_picking", admits)


@pytest.mark.parametrize(
    ("on", "reason"), [(True, "needs_spotify"), (False, "song_picking_unavailable")]
)
async def test_a_playlist_class_without_spotify_is_refused_with_no_job_or_credit(
    ienv: InterpretEnv, monkeypatch: pytest.MonkeyPatch, on: bool, reason: str
) -> None:
    picking(monkeypatch, on)
    before = ienv.counts()
    refused = await post_draft(ienv, PLAYLIST_MUSIC)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == reason
    assert ienv.counts() == before


@pytest.mark.parametrize("on", [True, False])  # False: the kill switch of Spotify writes
async def test_with_a_spotify_connection_the_playlist_class_is_admitted(
    ienv: InterpretEnv, monkeypatch: pytest.MonkeyPatch, on: bool
) -> None:
    picking(monkeypatch, on)
    await ienv.connect_spotify()
    made = await post_draft(ienv, PLAYLIST_MUSIC)
    assert made.status_code == 201, made.text


async def test_other_music_is_not_refused_for_a_missing_spotify_connection(
    ienv: InterpretEnv,
) -> None:
    # Before #1543 the soundless switch (off here) answered ``feature_off``; with no switch, "No
    # music" is admitted with no Spotify connection, so ``needs_spotify`` is not asked of it.
    before = ienv.counts()
    made = await post_draft(ienv, {"source": "none"})
    assert made.status_code == 201, made.text
    assert ienv.counts()[1] == before[1] + 1  # one job


async def test_an_interpretation_stored_before_amendment_25_still_designs(
    ienv: InterpretEnv,
) -> None:
    class_id, job_id = await ienv.interpreted()
    # Before Amendment 25 the request named no Spotify connection and the user has none now.
    stored = ienv.stored_input(job_id)
    stored.pop("spotify_connection_id")
    ienv.sql(
        "UPDATE jobs SET input = CAST(:i AS jsonb) WHERE id = :j", i=json.dumps(stored), j=job_id
    )
    ienv.sql("DELETE FROM connections WHERE owner_id = :o AND provider = 'spotify'", o=ienv.owner)
    done = await ienv.handoff(class_id)
    assert done.status_code in (200, 201, 202), done.text
    [design] = ienv.design_jobs(class_id)
    assert design.kind == "design_draft"
    assert ienv.stored_input(UUID(str(design.id)))["spotify_optional"] is True


async def test_a_browser_cannot_send_the_pre_amendment_flag(ienv: InterpretEnv) -> None:
    body = {
        "kind": DESIGN_DRAFT.name,
        "input": {
            "title": "A class",
            "brief": BRIEF,
            "music": PLAYLIST_MUSIC,
            "mowl_connection_id": str(ienv.renv.browser.connection_id),
            "spotify_optional": True,
        },
    }
    refused = await ienv.renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert refused.status_code == 422
