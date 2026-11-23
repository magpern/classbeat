"""#1424 (ADR 0042 sections 3 to 5): a Spotify draft accepts the editor's workout edits. The worker
stores the map it fitted the draft to (``music.edit_map``); ``POST /api/classes/{id}/edits`` checks
the edit against it, stores a ``user_edit`` revision with the one hash (its overlay included),
and refuses a stale base, a locked block, an audio edit and a draft with no map. Through the jobs
API, the worker side, Postgres as ``classbeat_app``, the fake MOWL and ``FakeProvider``; never the
network, and the web process reads no MOWL (the fake records every read it answers)."""

import json
from typing import Any
from uuid import UUID

import pytest

from classbeat.design import revision_edit_map
from classbeat.design.revision_hash import revision_hash
from classbeat.design.workout_edit_policy import blocks
from designtools import FakeMowlMusic
from revisetools import ReviseEnv
from test_refit_draft import correct_all, refit

pytestmark = pytest.mark.usefixtures("jenv")


async def edit(renv: ReviseEnv, class_id: UUID, base: object, op: dict[str, Any]) -> Any:
    body = {"base_revision_id": str(base), "edit": op}
    return await renv.browser.post(f"/api/classes/{class_id}/edits", body)


def zone_of(renv: ReviseEnv, class_id: UUID, revision_id: object, at: int) -> object:
    row = renv.sql(
        "SELECT content FROM class_revisions WHERE class_id = :c AND id = :r",
        c=class_id,
        r=revision_id,
    ).scalar_one()
    return blocks(row)[at].interval.get("zone")


def other_zone(renv: ReviseEnv, class_id: UUID, revision_id: object, at: int) -> int:
    return 2 if zone_of(renv, class_id, revision_id, at) != 2 else 3


def row_of(renv: ReviseEnv, revision_id: UUID) -> Any:
    return renv.sql("SELECT * FROM class_revisions WHERE id = :r", r=revision_id).one()


def count(renv: ReviseEnv, class_id: UUID) -> int:
    return renv.sql(
        "SELECT count(*) FROM class_revisions WHERE class_id = :c", c=class_id
    ).scalar_one()


def total_ms(content: dict[str, Any]) -> int:
    return sum(b.end_ms - b.start_ms for b in blocks(content))


async def test_the_worker_keeps_the_map_the_draft_was_fitted_to(renv: ReviseEnv) -> None:
    class_id, _base = await renv.design()
    [first] = renv.revisions(class_id)
    edit_map = first.music["edit_map"]
    assert {"version", "duration", "songs", "tracks", "crossfade"} <= set(edit_map)
    assert edit_map["version"] == first.provenance["map_version"]


async def test_an_edit_stores_a_user_edit_revision_with_its_override(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    zone = other_zone(renv, class_id, base, 1)
    answer = await edit(
        renv, class_id, base, {"op": "set", "block": 1, "field": "zone", "value": zone}
    )
    assert answer.status_code == 200, answer.text
    made = answer.json()
    new = UUID(made["revision_id"])
    assert renv.current(class_id) == new
    stored, before = row_of(renv, new), row_of(renv, base)
    assert stored.provenance["origin"] == "user_edit"
    assert stored.provenance["base_revision_id"] == str(base)
    assert stored.provenance["map_version"] == before.provenance["map_version"]
    assert stored.parent_id == base
    assert stored.job_id is None
    assert zone_of(renv, class_id, new, 1) == zone
    overlay = stored.edit_overlay
    assert [(r["type"], r["field"], r["value"]) for r in overlay["records"]] == [
        ("override", "zone", zone)
    ]
    assert bytes(stored.content_hash) == revision_hash(stored.content, overlay=overlay)
    assert made["content_hash"] == bytes(stored.content_hash).hex()
    assert "edit_map" not in stored.music  # a user_edit keeps none: the ancestor's is read
    assert {k: v for k, v in before.music.items() if k != "edit_map"} == stored.music
    assert [r["type"] for r in made["records"]] == ["override"]


async def test_the_stored_map_names_no_song(renv: ReviseEnv) -> None:
    """ADR 0006 section 6 and ADR 0007: a revision holds no Spotify id, title or artist, and
    ``class_revisions.music`` reaches the account export. The map keeps timing only."""
    class_id, base = await renv.design()
    stored = json.dumps(row_of(renv, base).music["edit_map"])
    for planted in (
        "PlantedTrk",
        "Invented Song",
        "Made-up Artist",
        '"spotify_id"',
        '"title"',
        '"artist"',
        "spotify:",
        "open.spotify.com",
    ):
        assert planted not in stored
    # The map still has everything the edit reads, and an edit of the draft still works.
    assert {"version", "duration", "crossfade", "songs", "tracks", "events"} <= set(
        row_of(renv, base).music["edit_map"]
    )
    answer = await edit(renv, class_id, base, {"op": "lock", "block": 0, "scope": "all"})
    assert answer.status_code == 200, answer.text


async def test_the_web_process_reads_no_mowl_for_an_edit(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, base = await renv.design()
    reads = len(fake_mowl.reads)
    zone = other_zone(renv, class_id, base, 1)
    answer = await edit(
        renv, class_id, base, {"op": "set", "block": 1, "field": "zone", "value": zone}
    )
    assert answer.status_code == 200, answer.text
    overlay = await renv.browser.get(f"/api/classes/{class_id}/overlay")
    assert overlay.status_code == 200, overlay.text
    assert len(fake_mowl.reads) == reads


async def test_a_drag_keeps_the_total_and_a_second_edit_uses_the_ancestors_map(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    was = row_of(renv, base).content
    first = await edit(renv, class_id, base, {"op": "drag", "block": 0, "delta_sec": 20})
    assert first.status_code == 200, first.text
    one = UUID(first.json()["revision_id"])
    assert total_ms(row_of(renv, one).content) == total_ms(was)
    (record,) = row_of(renv, one).edit_overlay["records"]
    assert record["field"] == "boundary"
    # The first edit's revision has no map; the second reads its nearest non-user_edit ancestor's.
    second = await edit(renv, class_id, one, {"op": "lock", "block": 1, "scope": "all"})
    assert second.status_code == 200, second.text
    two = UUID(second.json()["revision_id"])
    assert row_of(renv, two).parent_id == one
    assert [r["type"] for r in row_of(renv, two).edit_overlay["records"]] == ["override", "lock"]
    assert bytes(row_of(renv, two).content_hash) == revision_hash(
        row_of(renv, two).content, overlay=row_of(renv, two).edit_overlay
    )


async def test_a_stale_base_is_refused_with_the_current_id_and_stores_nothing(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    first = await edit(renv, class_id, base, {"op": "lock", "block": 0, "scope": "all"})
    assert first.status_code == 200, first.text
    stored = count(renv, class_id)
    again = await edit(renv, class_id, base, {"op": "lock", "block": 1, "scope": "all"})
    assert again.status_code == 409
    assert again.json()["detail"] == {
        "reason": "stale_revision",
        "current_revision_id": first.json()["revision_id"],
    }
    assert count(renv, class_id) == stored


async def test_a_locked_block_refuses_a_change_and_stores_nothing(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    locked = await edit(renv, class_id, base, {"op": "lock", "block": 1, "scope": "all"})
    assert locked.status_code == 200, locked.text
    current = UUID(locked.json()["revision_id"])
    stored = count(renv, class_id)
    zone = other_zone(renv, class_id, current, 1)
    refused = await edit(
        renv, class_id, current, {"op": "set", "block": 1, "field": "zone", "value": zone}
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "block_locked"
    assert count(renv, class_id) == stored
    assert renv.current(class_id) == current


@pytest.mark.parametrize(
    "op",
    [
        {"op": "pin_set", "piece": 0, "edge": "start", "requested_sec": 10.0, "mode": "exact"},
        {"op": "pin_clear", "piece": 0, "edge": "start"},
        {
            "op": "anchor",
            "block": 1,
            "edge": "start",
            "event": {"piece": 0, "source_ms": 30000, "kind": "drop"},
        },
        {"op": "remove_anchor", "block": 1, "edge": "start"},
    ],
)
async def test_an_audio_edit_is_refused_for_a_spotify_draft(
    renv: ReviseEnv, op: dict[str, Any]
) -> None:
    class_id, base = await renv.design()
    stored = count(renv, class_id)
    answer = await edit(renv, class_id, base, op)
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"]["reason"] == "audio_edit_unavailable"
    assert count(renv, class_id) == stored


async def test_a_draft_with_no_edit_map_is_unavailable_and_stores_nothing(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    renv.sql("UPDATE class_revisions SET music = music - 'edit_map' WHERE id = :r", r=base)
    stored = count(renv, class_id)
    answer = await edit(renv, class_id, base, {"op": "lock", "block": 0, "scope": "all"})
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "edit_unavailable")
    overlay = await renv.browser.get(f"/api/classes/{class_id}/overlay")
    assert (overlay.status_code, overlay.json()["detail"]["reason"]) == (409, "edit_unavailable")
    assert count(renv, class_id) == stored


@pytest.mark.parametrize("key", ["crossfade", "version", "duration", "tracks"])
async def test_a_map_that_does_not_read_is_unavailable_not_a_server_error(
    renv: ReviseEnv, key: str
) -> None:
    class_id, base = await renv.design()
    renv.sql(
        "UPDATE class_revisions SET music = music #- ARRAY['edit_map', :k] WHERE id = :r",
        k=key,
        r=base,
    )
    zone = other_zone(renv, class_id, base, 1)
    answer = await edit(
        renv, class_id, base, {"op": "set", "block": 1, "field": "zone", "value": zone}
    )
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "edit_unavailable")
    assert count(renv, class_id) == 1


async def test_a_map_the_library_could_not_have_made_is_never_checked_against(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    renv.sql(
        "UPDATE class_revisions SET music = jsonb_set(music, '{edit_map,version}', '\"x\"') "
        "WHERE id = :r",
        r=base,
    )
    answer = await edit(renv, class_id, base, {"op": "lock", "block": 0, "scope": "all"})
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "edit_unavailable")


async def test_a_duration_past_the_music_is_refused(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    longest = int(row_of(renv, base).music["songs"][-1]["end"])
    answer = await edit(renv, class_id, base, {"op": "duration", "total_sec": longest + 60})
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"]["reason"] == "class_length_out_of_tolerance"
    assert count(renv, class_id) == 1


async def test_a_tampered_draft_is_an_internal_error_and_stores_nothing(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    renv.sql("UPDATE class_revisions SET content_hash = :h WHERE id = :r", h=b"\x00" * 32, r=base)
    answer = await edit(renv, class_id, base, {"op": "lock", "block": 0, "scope": "all"})
    assert answer.status_code == 500
    assert count(renv, class_id) == 1


async def test_another_users_spotify_class_is_not_found(renv: ReviseEnv, browsers: Any) -> None:
    class_id, base = await renv.design()
    other = await browsers(mowl=False)
    stranger = await other.post(
        f"/api/classes/{class_id}/edits",
        {"base_revision_id": str(base), "edit": {"op": "lock", "block": 0, "scope": "all"}},
    )
    assert stranger.status_code == 404
    assert (await other.get(f"/api/classes/{class_id}/overlay")).status_code == 404
    assert count(renv, class_id) == 1


async def test_the_overlay_view_and_undo_of_an_edited_spotify_draft(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    made = await edit(renv, class_id, base, {"op": "lock", "block": 0, "scope": "all"})
    new = UUID(made.json()["revision_id"])
    view = await renv.browser.get(f"/api/classes/{class_id}/overlay")
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["revision_id"] == str(new)
    assert [r["type"] for r in body["records"]] == ["lock"]
    assert body["undo"]["revision_id"] == str(base)
    assert body["redo"] is None
    picked = await renv.browser.post(
        f"/api/classes/{class_id}/current",
        {
            "revision_id": str(base),
            "content_hash": body["undo"]["content_hash"],
            "expected_current_id": str(new),
        },
    )
    assert picked.status_code == 200, picked.text
    # Back on the first draft, a new edit branches: its map is the draft's own again.
    again = await edit(renv, class_id, base, {"op": "lock", "block": 1, "scope": "all"})
    assert again.status_code == 200, again.text
    assert row_of(renv, UUID(again.json()["revision_id"])).parent_id == base


async def test_a_duplicate_of_an_edited_draft_has_the_plain_hash_and_no_overlay(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    made = await edit(renv, class_id, base, {"op": "lock", "block": 0, "scope": "all"})
    edited = row_of(renv, UUID(made.json()["revision_id"]))
    body = {
        "revision_id": str(edited.id),
        "content_hash": bytes(edited.content_hash).hex(),
        "title": "Copy",
    }
    answer = await renv.browser.post(
        f"/api/classes/{class_id}/duplicate", body, {"Idempotency-Key": "k" * 20}
    )
    assert answer.status_code == 201, answer.text
    copy = row_of(renv, UUID(answer.json()["revision_id"]))
    assert copy.edit_overlay is None
    assert copy.content == edited.content
    assert bytes(copy.content_hash) == revision_hash(copy.content)


async def test_a_map_past_the_bound_is_not_stored_and_its_draft_stays_read_only(
    renv: ReviseEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(revision_edit_map, "MAX_BYTES", 100)
    class_id, base = await renv.design()  # the design still completes
    [first] = renv.revisions(class_id)
    assert "edit_map" not in first.music
    answer = await edit(renv, class_id, base, {"op": "lock", "block": 0, "scope": "all"})
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "edit_unavailable")


async def test_a_refit_stores_the_corrected_map_and_an_edit_of_it_is_checked_against_it(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    correct_all(renv, class_id)
    await refit(renv, class_id, base)
    first, refitted = renv.revisions(class_id)
    assert refitted.provenance["origin"] == "beat_one"
    assert refitted.music["edit_map"] != first.music["edit_map"], "the corrected map, not the old"
    assert refitted.music["edit_map"]["version"] == first.music["edit_map"]["version"]
    again = await edit(renv, class_id, refitted.id, {"op": "lock", "block": 1, "scope": "all"})
    assert again.status_code == 200, again.text


async def test_an_ancestor_on_other_music_gives_no_map(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    first = await edit(renv, class_id, base, {"op": "drag", "block": 0, "delta_sec": 20})
    one = UUID(first.json()["revision_id"])
    renv.sql(
        "UPDATE class_revisions "
        "SET music = jsonb_set(music, '{fingerprint}', to_jsonb(repeat('0', 64))) WHERE id = :r",
        r=base,
    )
    stored = count(renv, class_id)
    answer = await edit(renv, class_id, one, {"op": "lock", "block": 1, "scope": "all"})
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "edit_unavailable")
    assert count(renv, class_id) == stored
