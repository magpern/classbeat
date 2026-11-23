"""#1451 (ADR 0042 section 3, "On AI regeneration"): an AI revise, a song revise and a refit of an
edited Spotify draft carry the user's edit overlay instead of being refused. An override survives
with no lock; a draft that breaks a lock is ``overlay_violated``, a correction round; the new
revision stores the reconciled overlay under the one hash; undo and redo step over the revise;
"Clear all my edits" leaves a plain draft that is revised like any other; a tampered or unreadable
overlay fails the run closed with nothing stored; the stored map and overlay name no song. Through
the jobs API, the worker side, Postgres as ``classbeat_app``, the fake MOWL, the fake Spotify and
``FakeProvider``; never the network."""

import json
from typing import Any
from uuid import UUID

import pytest

from agentfake import CONFIG, SCRIPTS, FactoryModels, from_dict
from classbeat import design
from classbeat.agent.providers.fake import FakeProvider
from classbeat.design import Runtime
from classbeat.design.revision_hash import revision_hash
from classbeat.design.workout_edit_policy import blocks
from revisesongtools import changed_waiting, current, picked, revise_script
from revisetools import ReviseEnv
from songtools import SongEnv
from test_refit_draft import correct_all, refit
from test_spotify_draft_edit import count, edit, row_of

pytestmark = pytest.mark.usefixtures("jenv")

LOCK: dict[str, Any] = {"op": "lock", "block": 0, "scope": "all"}
MINE = 4  # the user's zone for block 0: the base has 2, ``revise_good`` sends 1
SET_ZONE: dict[str, Any] = {"op": "set", "block": 0, "field": "zone", "value": MINE}
PLANTED = (
    "PlantedTrk",
    "Invented Song",
    "Made-up Artist",
    '"spotify_id"',
    '"title"',
    '"artist"',
    "spotify:",
    "open.spotify.com",
)


def revise_data() -> dict[str, Any]:
    return json.loads((SCRIPTS / "revise_good.json").read_text("utf-8"))


def use_data(renv: ReviseEnv, data: dict[str, Any]) -> None:
    """Every run of the next jobs gets the script ``data`` (``ReviseEnv.use`` for a dict)."""

    def build() -> FakeProvider:
        made = from_dict(data)
        renv.providers.append(made)
        return made

    models = FactoryModels(build, running_covered=True, soundless_covered=True)
    runtime = Runtime(
        keyring=renv.denv.ops.keyring, models=models, agent=CONFIG, context_requests=10
    )
    design.configure(runtime)


def sent(renv: ReviseEnv) -> str:
    parts: list[str] = []
    for provider in renv.providers:
        for record in provider.sent:
            parts.append(record.spec.first_message)
            for results, reminder in record.turns:
                parts += [result.content for result in results]
                parts.append(reminder or "")
    return "\n".join(parts)


def zone_at(content: dict[str, Any], at: int) -> Any:
    return blocks(content)[at].interval.get("zone")


async def edited(renv: ReviseEnv, op: dict[str, Any]) -> tuple[UUID, UUID, UUID]:
    class_id, base = await renv.design()
    made = await edit(renv, class_id, base, op)
    assert made.status_code == 200, made.text
    return class_id, base, UUID(made.json()["revision_id"])


async def revised(renv: ReviseEnv, class_id: UUID, base: UUID) -> Any:
    made = await renv.ask(class_id, base)
    assert made.status_code == 201, made.text  # admitted: no ``has_user_edits`` any more
    await renv.run(design.REVISE_KIND)
    return renv.denv.ops.row(UUID(made.json()["id"]))


def held(stored: Any) -> None:
    """The one hash over the content and the stored overlay (NULL: the plain hash)."""
    assert bytes(stored.content_hash) == revision_hash(stored.content, overlay=stored.edit_overlay)


def names_no_song(stored: Any) -> None:
    text = json.dumps([stored.music.get("edit_map"), stored.edit_overlay])
    for planted in PLANTED:
        assert planted not in text


async def test_a_revise_keeps_the_users_zone_with_no_lock(renv: ReviseEnv) -> None:
    class_id, _first, base = await edited(renv, SET_ZONE)
    renv.use("revise_good")
    row = await revised(renv, class_id, base)
    assert (row.state, row.error_category) == ("completed", None)
    new = renv.current(class_id)
    assert new not in (None, base)
    stored = row_of(renv, new)  # pyright: ignore[reportArgumentType]
    assert (stored.parent_id, stored.provenance["origin"]) == (base, "revise_draft")
    assert zone_at(stored.content, 0) == MINE  # the model sent 1; the user's 4 is kept
    assert blocks(stored.content)[0].interval.get("cue") == "Settle in easy."  # the model's own
    (record,) = stored.edit_overlay["records"]
    assert (record["field"], record["value"], record["ai_value_at_set"]) == ("zone", MINE, 1)
    held(stored)
    names_no_song(stored)
    assert stored.music["edit_map"] is not None  # the revised draft is editable again
    told = sent(renv)
    assert "The instructor's own edits are fixed" in told
    assert f"set zone = {MINE}" in told


async def test_a_draft_breaking_a_lock_is_a_correction_round(renv: ReviseEnv) -> None:
    class_id, first, base = await edited(renv, LOCK)
    data = revise_data()
    kept = json.loads(json.dumps(data["drafts"]["revised"]))
    kept["segments"][0]["intervals"][0] = row_of(renv, first).content["segments"][0]["intervals"][0]
    kept["segments"][1]["intervals"][0]["zone"] = 5  # a change of its own elsewhere
    data["drafts"]["kept"] = kept
    submit = data["turns"][-1]["calls"][0]
    breach = {**submit, "expect": {"ok": False, "error": "overlay_violated"}}
    data["turns"][-1] = {"calls": [breach]}
    data["turns"].append(
        {"calls": [{**submit, "input": {**submit["input"], "draft": {"$draft": "kept"}}}]}
    )
    use_data(renv, data)
    row = await revised(renv, class_id, base)
    assert (row.state, row.error_category) == ("completed", None)  # the second submit kept it
    new = renv.current(class_id)
    stored = row_of(renv, new)  # pyright: ignore[reportArgumentType]
    assert (
        stored.content["segments"][0]["intervals"][0]
        == (row_of(renv, base).content["segments"][0]["intervals"][0])
    )
    assert [r["type"] for r in stored.edit_overlay["records"]] == ["lock"]
    held(stored)
    assert "locked by the instructor" in sent(renv)


async def test_undo_and_redo_step_over_the_revise(renv: ReviseEnv) -> None:
    class_id, _first, base = await edited(renv, SET_ZONE)
    renv.use("revise_good")
    await revised(renv, class_id, base)
    new = renv.current(class_id)
    view = (await renv.browser.get(f"/api/classes/{class_id}/overlay")).json()
    assert view["undo"]["revision_id"] == str(base)
    back = await renv.browser.post(
        f"/api/classes/{class_id}/current",
        {
            "revision_id": str(base),
            "content_hash": view["undo"]["content_hash"],
            "expected_current_id": str(new),
        },
    )
    assert back.status_code == 200, back.text
    view = (await renv.browser.get(f"/api/classes/{class_id}/overlay")).json()
    assert [r["field"] for r in view["records"]] == ["zone"]  # the edited base, its edit kept
    assert view["redo"]["revision_id"] == str(new)
    again = await renv.browser.post(
        f"/api/classes/{class_id}/current",
        {
            "revision_id": str(new),
            "content_hash": view["redo"]["content_hash"],
            "expected_current_id": str(base),
        },
    )
    assert again.status_code == 200, again.text
    assert renv.current(class_id) == new


async def test_after_clear_all_the_draft_is_revised_as_a_plain_one(renv: ReviseEnv) -> None:
    class_id, _first, locked = await edited(renv, LOCK)
    cleared = await edit(renv, class_id, locked, {"op": "clear_all"})
    assert cleared.status_code == 200, cleared.text
    base = UUID(cleared.json()["revision_id"])
    assert row_of(renv, base).provenance["origin"] == "user_edit"  # itself a user edit
    assert row_of(renv, base).edit_overlay is None
    renv.use("revise_good")
    row = await revised(renv, class_id, base)
    assert (row.state, row.error_category) == ("completed", None)
    stored = row_of(renv, renv.current(class_id))  # pyright: ignore[reportArgumentType]
    assert (stored.parent_id, stored.edit_overlay) == (base, None)
    assert zone_at(stored.content, 0) == 1  # nothing kept: the model's zone
    held(stored)


async def test_the_revised_draft_is_edited_on_its_own_map(renv: ReviseEnv) -> None:
    class_id, _first, base = await edited(renv, SET_ZONE)
    renv.use("revise_good")
    await revised(renv, class_id, base)
    new = renv.current(class_id)
    again = await edit(renv, class_id, new, LOCK)
    assert again.status_code == 200, again.text
    stored = row_of(renv, UUID(again.json()["revision_id"]))
    assert sorted(r["type"] for r in stored.edit_overlay["records"]) == ["lock", "override"]
    # An unreadable map leaves the draft read-only: the edit fails closed, nothing stored.
    renv.sql(
        "UPDATE class_revisions SET music = jsonb_set(music, '{edit_map}', '{\"tracks\": 7}') "
        "WHERE id = :r",
        r=new,
    )
    stored_before = count(renv, class_id)
    refused = await edit(renv, class_id, stored.id, {"op": "unlock", "block": 0})
    assert refused.json()["detail"]["reason"] == "edit_unavailable", refused.text
    assert count(renv, class_id) == stored_before


@pytest.mark.parametrize(
    "overlay",
    [
        pytest.param({"tamper": True}, id="tampered"),
        pytest.param({"version": 1, "records": [{"type": "bogus"}]}, id="unreadable"),
    ],
)
async def test_a_bad_overlay_fails_the_revise_closed(
    renv: ReviseEnv, overlay: dict[str, Any]
) -> None:
    class_id, _first, base = await edited(renv, LOCK)
    row = row_of(renv, base)
    if overlay.get("tamper"):  # the stored hash no longer holds
        changed = json.loads(json.dumps(row.edit_overlay))
        changed["records"][0]["scope"] = "timing"
        params = {"o": json.dumps(changed), "h": bytes(row.content_hash)}
    else:  # the hash holds, but the overlay does not read
        params = {"o": json.dumps(overlay), "h": revision_hash(row.content, overlay=overlay)}
    renv.sql(
        "UPDATE class_revisions SET edit_overlay = CAST(:o AS jsonb), content_hash = :h "
        "WHERE id = :r",
        r=base,
        **params,
    )
    stored = count(renv, class_id)
    renv.use("revise_good")
    done = await revised(renv, class_id, base)  # admitted on the stored hash
    assert (done.state, done.error_category) == ("failed", "internal")
    assert (renv.current(class_id), count(renv, class_id)) == (base, stored)


async def test_a_refit_keeps_the_users_edits(renv: ReviseEnv) -> None:
    class_id, _first, base = await edited(renv, SET_ZONE)
    correct_all(renv, class_id)
    job_id = await refit(renv, class_id, base)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    stored = row_of(renv, renv.current(class_id))  # pyright: ignore[reportArgumentType]
    assert (stored.parent_id, stored.provenance["origin"]) == (base, "beat_one")
    assert zone_at(stored.content, 0) == MINE
    assert [r["field"] for r in stored.edit_overlay["records"]] == ["zone"]
    held(stored)
    names_no_song(stored)


async def test_a_song_revise_keeps_the_users_edits(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, first, _list = await picked(songenv, rider)
    made = await rider.browser.post(
        f"/api/classes/{class_id}/edits", {"base_revision_id": str(first), "edit": SET_ZONE}
    )
    assert made.status_code == 200, made.text
    base = UUID(made.json()["revision_id"])
    job_id, list_id, digest = await changed_waiting(songenv, rider, class_id, base)
    assert (await rider.approve(job_id, list_id, digest)).status_code == 200
    songenv.script = revise_script()
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    new = current(songenv, class_id)
    stored = songenv.revision(new)
    assert (stored.parent_id, stored.provenance["origin"]) == (base, "revise_songs")
    assert zone_at(stored.content, 0) == MINE
    (record,) = stored.edit_overlay["records"]
    assert (record["field"], record["value"]) == ("zone", MINE)
    assert bytes(stored.content_hash) == revision_hash(stored.content, overlay=stored.edit_overlay)
