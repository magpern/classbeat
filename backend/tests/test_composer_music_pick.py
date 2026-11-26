"""``PUT /api/classes/{id}/music`` with ``choice: "pick"`` ("choose it for me") needs a hint of
what to play (#1538, owner rule 2026-11-26: songs are never picked without a hint from the
instructor): a taste item, a pinned song, a named playlist or mix, the form's source or attached
playlists. Without one it is refused ``music_unanswered`` and nothing is stored; every other
answer is unchanged. Against Postgres, the fake MOWL and Spotify; never the network or a model."""

import json
from typing import Any
from uuid import UUID

import httpx
import pytest

from classbeat.design.music_hint_policy import pick_allowed
from composertools import ComposerEnv
from songtools import Rider

UNDECIDED: dict[str, Any] = {"state": "undecided"}
SOURCE = "0123456789abcdefABCDEF"
NAMED = {"source": "mowl_spotify_playlist", "spotify_playlist_id": SOURCE}


async def put_music(rider: Rider, class_id: UUID, body: Any) -> httpx.Response:
    browser = rider.browser
    response = await browser.client.put(
        f"/api/classes/{class_id}/music", headers=await browser.headers(), json=body
    )
    browser.client.cookies.clear()
    return response


def stored_decision(cenv: ComposerEnv, class_id: UUID) -> Any:
    return cenv.sql(
        "SELECT interpretation -> 'music_decision' FROM classes WHERE id = :c", c=class_id
    ).scalar()


def edit(cenv: ComposerEnv, class_id: UUID, **changes: Any) -> None:
    """Change the stored reading (``brief``, ``music``, ``items``) with the class's own column."""
    for key, value in changes.items():
        cenv.sql(
            "UPDATE classes SET interpretation = jsonb_set(interpretation, ARRAY[:k], "
            "CAST(:v AS jsonb)) WHERE id = :c",
            k=key,
            v=json.dumps(value),
            c=class_id,
        )


def given(cenv: ComposerEnv, class_id: UUID, **fields: Any) -> None:
    """Add fields to the interpret job's stored input (what the form gave)."""
    cenv.sql(
        "UPDATE jobs SET input = input || CAST(:f AS jsonb) "
        "WHERE class_id = :c AND kind = 'interpret_request'",
        f=json.dumps(fields),
        c=class_id,
    )


@pytest.mark.usefixtures("jenv")
async def test_a_pick_with_no_hint_is_refused_and_stores_nothing(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider, decision=UNDECIDED)
    before = cenv.songenv.counts()
    refused = await put_music(rider, class_id, {"choice": "pick"})
    assert (refused.status_code, refused.json()["detail"]) == (409, {"reason": "music_unanswered"})
    assert stored_decision(cenv, class_id) == UNDECIDED
    assert cenv.songenv.counts() == before


@pytest.mark.usefixtures("jenv")
async def test_a_form_pick_is_no_hint(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider, decision=UNDECIDED)
    given(cenv, class_id, music_override={"source": "pick"})
    refused = await put_music(rider, class_id, {"choice": "pick"})
    assert (refused.status_code, refused.json()["detail"]) == (409, {"reason": "music_unanswered"})
    assert stored_decision(cenv, class_id) == UNDECIDED


@pytest.mark.usefixtures("jenv")
async def test_a_pick_with_attached_playlists_is_stored(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider, sources=[SOURCE], decision=UNDECIDED)
    stored = await put_music(rider, class_id, {"choice": "pick"})
    assert stored.status_code == 200, stored.text
    decided = stored_decision(cenv, class_id)
    assert (decided["state"], decided["choice"]) == ("decided", "pick")


@pytest.mark.usefixtures("jenv")
async def test_a_pick_with_a_pinned_song_is_stored(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider, pinned=[{"title": "Africa"}], decision=UNDECIDED)
    assert (await put_music(rider, class_id, {"choice": "pick"})).status_code == 200


@pytest.mark.usefixtures("jenv")
async def test_a_pick_with_a_taste_in_the_brief_is_stored(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider, decision=UNDECIDED)
    brief = cenv.sql("SELECT brief FROM classes WHERE id = :c", c=class_id).scalar_one()
    edit(cenv, class_id, brief={**brief, "music_taste": {"hits": True}})
    assert (await put_music(rider, class_id, {"choice": "pick"})).status_code == 200


@pytest.mark.usefixtures("jenv")
async def test_a_pick_with_a_taste_item_of_the_reading_is_stored(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider, decision=UNDECIDED)
    item = {"path": "music_genre.0", "origin": "explicit", "value": "rock"}
    edit(cenv, class_id, items=[item])
    assert (await put_music(rider, class_id, {"choice": "pick"})).status_code == 200


@pytest.mark.usefixtures("jenv")
async def test_a_pick_on_a_named_playlist_is_stored(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider, decision=UNDECIDED)
    edit(cenv, class_id, music=NAMED)
    assert (await put_music(rider, class_id, {"choice": "pick"})).status_code == 200


@pytest.mark.usefixtures("jenv")
@pytest.mark.parametrize(
    "body",
    [
        {"choice": "later"},
        {"choice": "describe", "note": "Dark techno"},
        {"choice": "playlists", "playlists": [{"kind": "spotify_playlist", "id": SOURCE}]},
    ],
    ids=["later", "describe", "playlists"],
)
async def test_every_other_answer_needs_no_hint(cenv: ComposerEnv, body: dict[str, Any]) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider, decision=UNDECIDED)
    assert (await put_music(rider, class_id, body)).status_code == 200


@pytest.mark.parametrize(
    ("music", "pinned", "override", "sources", "allowed"),
    [
        ({"source": "pick"}, 0, None, None, False),
        (None, 0, None, None, False),  # no music source at all: needs a hint too
        ({"source": "pick"}, 0, "pick", None, False),
        ({"source": "pick"}, 1, None, None, True),
        ({"source": "pick"}, 0, None, [{"id": "x"}], True),
        ({"source": "pick"}, 0, "none", None, True),
        (NAMED, 0, None, None, True),
        ({"source": "mowl_swedebeat_mix", "mix_id": "m"}, 0, None, None, True),
    ],
)
def test_pick_allowed_needs_a_hint_or_a_named_source(
    music: dict[str, str] | None,
    pinned: int,
    override: str | None,
    sources: Any,
    allowed: bool,
) -> None:
    assert (
        pick_allowed(music, [], None, pinned=pinned, override=override, sources=sources) is allowed
    )
