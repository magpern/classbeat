"""A model job resolves its route before any MOWL or Spotify request (ADR 0012 section 5,
"Resolving"; #215). An unusable route fails ``model_config`` with nothing sent: ``design_draft``
is covered in ``test_models_jobs``; here ``revise_draft`` and each phase of the song kinds, with a
model source that refuses every group. Moving a resolution after the first request fails these.
"""

from dataclasses import replace
from uuid import UUID

import pytest

from agentfake import RefusingModels
from classbeat import design
from classbeat.design import kind as design_kind
from revisesongtools import change, changed_approved, current, picked, replace_script
from revisetools import ReviseEnv
from songtools import SongEnv


def refuse() -> RefusingModels:
    """From now on the worker's runtime refuses every stage group."""
    runtime = design_kind.current_runtime()
    assert runtime is not None
    models = RefusingModels()
    design.configure(replace(runtime, models=models))
    return models


async def test_revise_draft_resolves_its_route_before_the_music_is_read(
    renv: ReviseEnv, fake_mowl: object
) -> None:
    class_id, base = await renv.design()
    requests = len(fake_mowl.requests)  # pyright: ignore[reportAttributeAccessIssue]
    renv.use("revise_good")
    models = refuse()
    made = await renv.ask(class_id, base)
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["id"])
    await renv.run(design.REVISE_KIND)

    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "model_config")
    assert models.asked == [("pro", "design")]
    assert len(fake_mowl.requests) == requests  # pyright: ignore[reportAttributeAccessIssue]
    assert renv.denv.agent_runs(job_id) == []


@pytest.mark.usefixtures("songenv")
async def test_the_pick_phase_resolves_the_songs_group_before_any_request(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    job_id = await songenv.created(rider)
    before = songenv.counts()
    models = refuse()
    row = await songenv.run(job_id)

    assert (row.state, row.error_category) == ("failed", "model_config")
    assert models.asked == [("pro", "songs")]
    assert songenv.counts() == before  # no Spotify, no MOWL, no model turn


@pytest.mark.usefixtures("songenv")
async def test_the_design_phase_resolves_the_design_group_before_any_request(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    job_id, _ = await songenv.approved(rider)
    before = songenv.counts()
    models = refuse()
    row = await songenv.run(job_id)

    assert (row.state, row.error_category) == ("failed", "model_config")
    assert models.asked == [("pro", "design")]
    assert songenv.counts() == before


@pytest.mark.usefixtures("songenv")
async def test_change_songs_resolves_the_songs_group_before_any_request(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    songenv.script = replace_script()
    answer = await change(rider, class_id, base)
    assert answer.status_code == 201, answer.text
    job_id = UUID(answer.json()["id"])
    before = songenv.counts()
    models = refuse()
    row = await songenv.run(job_id)

    assert (row.state, row.error_category) == ("failed", "model_config")
    assert models.asked == [("pro", "songs")]
    assert songenv.counts() == before


@pytest.mark.usefixtures("songenv")
async def test_the_revise_phase_of_change_songs_resolves_the_design_group_first(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    class_id, _, _ = await picked(songenv, rider)
    job_id, _ = await changed_approved(songenv, rider, class_id, current(songenv, class_id))
    before = songenv.counts()
    models = refuse()
    row = await songenv.run(job_id)

    assert (row.state, row.error_category) == ("failed", "model_config")
    assert models.asked == [("pro", "design")]
    assert songenv.counts() == before


async def test_a_browser_cannot_send_a_route_key_to_revise_draft(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    jobs = "SELECT count(*) FROM jobs WHERE owner_id = :o"
    before = renv.sql(jobs, o=renv.owner).scalar_one()
    refused = await renv.ask(class_id, base, route_key="pro")
    assert refused.status_code == 422, refused.text
    assert renv.sql(jobs, o=renv.owner).scalar_one() == before


@pytest.mark.usefixtures("songenv")
async def test_a_browser_cannot_send_a_route_key_to_the_song_kinds(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    pick = await rider.create(route_key="pro")
    assert pick.status_code == 422, pick.text
    class_id, base, _ = await picked(songenv, rider)
    jobs = "SELECT count(*) FROM jobs WHERE owner_id = :o"
    before = songenv.sql(jobs, o=rider.user_id).scalar_one()
    changed = await change(rider, class_id, base, route_key="pro")
    assert changed.status_code == 422, changed.text
    assert songenv.sql(jobs, o=rider.user_id).scalar_one() == before
