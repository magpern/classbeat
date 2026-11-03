"""The plan's class length cap and feature gates through the jobs API and the worker (#496).

Every route that can create or change a brief is gated: ``design_draft`` and
``design_with_songs`` check their own brief, ``revise_draft`` and ``revise_songs`` the class's
stored brief, all in ``with_plan`` before the kind's own checks (409 ``quota`` ``length`` or
``feature``, nothing created). The job carries the plan's gates (a server field a browser can never
send) and the worker refuses every draft with a game, an FTP test or a length the plan does not
include, so a revise that asks for games stores no game. An admin's change applies to the next
request. The users start on ``pro`` (``plantools.users_plan``); a revoke puts one on ``free``."""

import copy
import json
import uuid
from typing import Any
from uuid import UUID

import pytest

import revisetools
from agentfake import from_dict, script
from classbeat import design, songs
from classbeat.context import UserContext
from classbeat.db import user_session
from classbeat.jobs.credits import QuotaRefused
from classbeat.songs.inputs import DesignWithSongsInput, ReviseSongsInput
from ctx import make_context
from dbtools import migration_module
from designtools import BRIEF
from jobapi import new_key
from jobtools import grant_plan
from optools import PLAYLIST
from revisetools import ReviseEnv

FREE_GATES = {"max_class_minutes": 30, "games": False, "races": True, "ftp_tests": True}
PRO_GATES = {"max_class_minutes": 90, "games": True, "races": True, "ftp_tests": True}


@pytest.fixture(autouse=True)
def seeded_plans(renv: ReviseEnv) -> Any:
    yield
    renv.sql("UPDATE plans SET races_allowed = true, ftp_tests_allowed = true")
    renv.sql(migration_module("plan_gates").SEED)


def to_free(renv: ReviseEnv) -> None:
    renv.sql("UPDATE entitlements SET revoked_at = now() WHERE owner_id = :o", o=renv.owner)


def design_body(renv: ReviseEnv, **brief: Any) -> dict[str, Any]:
    return {
        "kind": design.KIND,
        "input": {
            "title": "Tuesday climb",
            "brief": {**BRIEF, **brief},
            "music": {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST},
            "mowl_connection_id": str(renv.browser.connection_id),
        },
    }


async def create(renv: ReviseEnv, body: dict[str, Any]) -> Any:
    return await renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


def counts(renv: ReviseEnv) -> tuple[int, int]:
    jobs = renv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=renv.owner).scalar_one()
    classes = renv.sql(
        "SELECT count(*) FROM classes WHERE owner_id = :o", o=renv.owner
    ).scalar_one()
    return jobs, classes


# --- admit ------------------------------------------------------------------------------------


async def test_a_design_over_the_free_cap_is_refused_and_creates_nothing(renv: ReviseEnv) -> None:
    to_free(renv)
    before = counts(renv)
    refused = await create(renv, design_body(renv, length_min=45))
    assert (refused.status_code, refused.json()) == (
        409,
        {"detail": {"reason": "quota", "quota": "length", "limit": 30, "used": 45, "plan": "free"}},
    )
    assert counts(renv) == before
    made = await create(renv, design_body(renv, length_min=30))
    assert made.status_code == 201, made.text
    assert renv.denv.ops.row(UUID(made.json()["id"])).input["plan_gates"] == FREE_GATES


async def test_an_admin_change_applies_to_the_next_request(renv: ReviseEnv) -> None:
    to_free(renv)
    assert (await create(renv, design_body(renv, length_min=45))).status_code == 409
    renv.sql("UPDATE plans SET max_class_minutes = 45 WHERE id = 'free'")
    made = await create(renv, design_body(renv, length_min=45))
    assert made.status_code == 201, made.text
    stored = renv.denv.ops.row(UUID(made.json()["id"])).input["plan_gates"]
    assert stored == {**FREE_GATES, "max_class_minutes": 45}


async def test_races_on_a_plan_without_them_are_a_feature_refusal(renv: ReviseEnv) -> None:
    renv.sql("UPDATE plans SET races_allowed = false WHERE id = 'pro'")
    refused = await create(renv, design_body(renv, races=1))
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        {"reason": "quota", "quota": "feature", "feature": "races", "plan": "pro"},
    )
    renv.sql("UPDATE plans SET ftp_tests_allowed = false WHERE id = 'pro'")
    refused = await create(renv, design_body(renv, ftp_test=5))
    assert refused.json()["detail"]["feature"] == "ftp_tests"
    made = await create(renv, design_body(renv))
    assert made.status_code == 201, made.text
    gates = renv.denv.ops.row(UUID(made.json()["id"])).input["plan_gates"]
    assert gates == {**PRO_GATES, "races": False, "ftp_tests": False}


async def test_a_browser_can_never_send_the_gates(renv: ReviseEnv) -> None:
    before = counts(renv)
    for value in (PRO_GATES, None, {"max_class_minutes": 180}):
        body = design_body(renv)
        body["input"]["plan_gates"] = value
        assert (await create(renv, body)).status_code == 422
    assert counts(renv) == before


async def test_a_revise_of_a_class_longer_than_the_plan_allows_is_refused(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    # The class was designed for 45 minutes on a bigger plan; the user is on free now.
    stored = {**BRIEF, "sport": "cycling", "length_min": 45}
    renv.sql(
        "UPDATE classes SET brief = CAST(:b AS jsonb) WHERE id = :c",
        b=json.dumps(stored),
        c=class_id,
    )
    to_free(renv)
    refused = await renv.ask(class_id, base)
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        {"reason": "quota", "quota": "length", "limit": 30, "used": 45, "plan": "free"},
    )
    assert (
        renv.sql("SELECT count(*) FROM class_messages WHERE class_id = :c", c=class_id).scalar_one()
        == 0
    )
    grant_plan(renv.denv.admin, renv.owner, "pro")  # 90 minutes again
    assert (await renv.ask(class_id, base)).status_code == 201


async def test_the_song_kinds_check_the_brief_before_anything_else(renv: ReviseEnv) -> None:
    """``design_with_songs`` checks its own brief and ``revise_songs`` the class's stored one, in
    ``with_plan``, before the song-picking flag or a connection is even read."""
    class_id, base = await renv.design()
    renv.sql(
        "UPDATE classes SET brief = CAST(:b AS jsonb) WHERE id = :c",
        b=json.dumps({**BRIEF, "sport": "cycling", "length_min": 45}),
        c=class_id,
    )
    to_free(renv)
    ids = {"mowl_connection_id": uuid.uuid4(), "spotify_connection_id": uuid.uuid4()}
    pick = DesignWithSongsInput.model_validate(
        {"title": "Songs", "brief": {**BRIEF, "length_min": 45}, **ids}
    )
    change = ReviseSongsInput.model_validate(
        {"class_id": class_id, "base_revision_id": base, "replace": [0], "request": "Other", **ids}
    )
    ctx: UserContext = make_context(renv.owner)
    for kind, parsed in ((songs.DESIGN_WITH_SONGS, pick), (songs.REVISE_SONGS, change)):
        assert kind.admit is not None
        async with user_session(renv.denv.ops.factory, ctx) as db:
            with pytest.raises(QuotaRefused) as refused:
                await kind.admit(db, ctx, parsed)
        assert refused.value.detail() == {
            "quota": "length", "limit": 30, "used": 45, "plan": "free",
        }  # fmt: skip


# --- the worker: games ------------------------------------------------------------------------


def game_scripts() -> dict[str, dict[str, Any]]:
    """Two runs that try a game: ``refused`` expects the plan to refuse it and submits the class
    without it; ``accepted`` expects the plan to take it. A run that meets the other answer fails
    its script (``ScriptMismatch``), so the job fails."""
    out: dict[str, dict[str, Any]] = {}
    for base_name, key, extra in (
        ("good_first_draft", "good", {}),
        ("revise_good", "revised", {"note": "Added a game."}),
    ):
        made = copy.deepcopy(script(base_name).model_dump(mode="json"))
        plain = made["drafts"][key]
        game = copy.deepcopy(plain)
        game["segments"][1]["intervals"][0]["game"] = "racetrack"
        made["drafts"] = {"plain": plain, "game": game}
        refused = {"ok": False, "error": "not_allowed"}

        def turn(
            name: str,
            draft: str,
            expect: dict[str, Any] | None,
            more: dict[str, Any] = extra,
        ) -> dict[str, Any]:
            call: dict[str, Any] = {"name": name, "input": {"draft": {"$draft": draft}}}
            if name == "submit_draft":
                call["input"].update(more)
            if expect is not None:
                call["expect"] = expect
            return {"calls": [call]}

        out[f"{base_name}_refused"] = {
            **made,
            "turns": [
                turn("preview_draft", "game", refused),
                turn("submit_draft", "game", refused),
                turn("submit_draft", "plain", None),
            ],
        }
        out[f"{base_name}_accepted"] = {
            **made,
            "turns": [
                turn("preview_draft", "game", {"ok": True}),
                turn("submit_draft", "game", None),
            ],
        }
    return out


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> None:
    extra = game_scripts()
    original = revisetools.provider
    monkeypatch.setattr(
        revisetools,
        "provider",
        lambda name: from_dict(extra[name]) if name in extra else original(name),
    )


def has_game(content: Any) -> bool:
    return '"game"' in json.dumps(content)


@pytest.mark.usefixtures("scripted")
async def test_a_design_on_free_never_stores_a_game(renv: ReviseEnv) -> None:
    to_free(renv)
    class_id, revision_id = await renv.design("good_first_draft_refused")
    [stored] = renv.revisions(class_id)
    assert stored.id == revision_id
    assert not has_game(stored.content)


@pytest.mark.usefixtures("scripted")
async def test_a_design_on_pro_may_have_a_game(renv: ReviseEnv) -> None:
    class_id, _ = await renv.design("good_first_draft_accepted")
    assert has_game(renv.revisions(class_id)[0].content)


@pytest.mark.usefixtures("scripted")
async def test_a_revise_that_asks_for_games_on_free_stores_no_game(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    to_free(renv)
    job_id, _ = await renv.revise(class_id, base, "revise_good_refused", "Add a game, please.")
    assert renv.denv.ops.row(job_id).state == "completed"
    assert renv.denv.ops.row(job_id).input["plan_gates"] == FREE_GATES
    revised = renv.revisions(class_id)[-1]
    assert revised.number == 2
    assert not has_game(revised.content)


@pytest.mark.usefixtures("scripted")
async def test_a_revise_that_asks_for_games_on_pro_gets_them(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    job_id, _ = await renv.revise(class_id, base, "revise_good_accepted", "Add a game, please.")
    assert renv.denv.ops.row(job_id).state == "completed"
    assert has_game(renv.revisions(class_id)[-1].content)


@pytest.mark.usefixtures("scripted")
async def test_the_gates_are_the_ones_the_job_was_created_with(renv: ReviseEnv) -> None:
    """A plan change after creation does not change a job that is already queued: it runs under
    the gates it was admitted with (as its ``route_key``)."""
    class_id, base = await renv.design()
    to_free(renv)
    renv.use("revise_good_refused")
    made = await renv.ask(class_id, base, "Add a game, please.")
    assert made.status_code == 201, made.text
    renv.sql("UPDATE plans SET games_allowed = true WHERE id = 'free'")  # after creation
    await renv.run(design.REVISE_KIND)
    assert renv.denv.ops.row(UUID(made.json()["id"])).state == "completed"
    assert not has_game(renv.revisions(class_id)[-1].content)
