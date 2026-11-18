"""Every reader of a composer's ``v: 3`` list handles its ``preview`` (ADR 0038 section 5, #1188):
the songs view answers it with the design, each song's span and source; ``song_list.load`` (the
one reader of the approval check, the seed of a swap or change, ``revise_songs`` and the match,
and phase 2's ``prepare``) reads it with its stored hash re-checked over the whole content; the
studio's reader never takes it for a studio list. Never the network or a model."""

import pytest

from classbeat.db import user_session
from classbeat.songs import song_list
from classbeat.swedebeat_studio import song_list as studio_list
from composertools import ComposerEnv
from ctx import make_context

pytestmark = pytest.mark.usefixtures("jenv")


async def test_the_readers_handle_the_preview(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    _, list_id, digest = await cenv.waiting(rider, class_id)
    shown = await rider.browser.get(f"/api/classes/{class_id}/revisions/{list_id}")
    assert shown.status_code == 200, shown.text
    body = shown.json()
    assert body["content_hash"] == digest
    assert body["preview"]["content"]["segments"]
    assert len(body["preview"]["songs"]) == len(body["songs"])
    assert {s["source"]["kind"] for s in body["songs"]} == {"search"}
    ctx = make_context(rider.user_id)
    async with user_session(cenv.songenv.factory, ctx) as db:
        loaded = await song_list.load(db, rider.user_id, class_id, list_id)
        studio = await studio_list.load(db, rider.user_id, class_id, list_id)
    assert loaded is not None
    assert loaded.content.preview is not None
    assert loaded.content_hash.hex() == digest
    assert studio is None
    cenv.sql(  # the hash covers the preview: a changed design no longer loads
        "UPDATE class_revisions SET content = jsonb_set(content, '{preview,content,name}', "
        "'\"Other\"') WHERE id = :r",
        r=list_id,
    )
    async with user_session(cenv.songenv.factory, ctx) as db:
        assert await song_list.load(db, rider.user_id, class_id, list_id) is None
