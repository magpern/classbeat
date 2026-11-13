"""Change songs reads its request like a revise (ADR 0029 section 4, issue #1023): a class that
states values has the request read by the ``interpret`` stage and layered onto its brief, in phase 1
(the pick is sized by it and the list keeps the reading) and in phase 2 (the same reading, never a
second model call, also for a resume). Against the fake Spotify, the fake MOWL and ``FakeProvider``
scripts; no network, no real account."""

import copy
import json
from typing import Any
from uuid import UUID

import pytest

from classbeat import songs
from classbeat.agent import AgentConfig
from classbeat.agent.limits import Limits
from classbeat.songs import revise as revise_songs_mod
from classbeat.songs import revise_reading
from classbeat.songs.config import SongConfig
from revisesongtools import (
    REQUEST,
    change,
    current,
    picked,
    replace_script,
    resume_change,
    revise_script,
)
from songtools import SongEnv, failing_script

pytestmark = pytest.mark.usefixtures("songenv")

# The class states its 30 minutes; the request states one climb (the revised draft has one).
READING = {"climbs": {"value": 1, "origin": "explicit", "quote": "A harder song"}}
LONGER = {"length_min": {"value": 25, "origin": "explicit"}}


def short_script(reading: dict[str, Any] | None = None) -> dict[str, Any]:
    """Phase 2's ``revise_good`` with an interpret turn that reads ``reading``."""
    data = copy.deepcopy(revise_script())
    data["turns"][0]["calls"][0]["input"] = reading or {}
    return data


async def stated(songenv: SongEnv, rider: Any) -> tuple[UUID, UUID]:
    """A class on picked songs whose brief states its length: (class id, its draft)."""
    class_id, base, _ = await picked(songenv, rider)
    songenv.sql(
        "UPDATE classes SET brief = brief || CAST(:s AS jsonb) WHERE id = :c",
        s=json.dumps({"explicit": ["length_min"]}),
        c=class_id,
    )
    return class_id, base


async def waiting(
    songenv: SongEnv, rider: Any, class_id: UUID, base: UUID, reading: dict[str, Any] | None
) -> tuple[UUID, UUID, str]:
    songenv.script = replace_script(reading=reading)
    answer = await change(rider, class_id, base)
    assert answer.status_code == 201, answer.text
    job_id = UUID(answer.json()["id"])
    row = await songenv.run(job_id)
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    return job_id, row.approval_revision_id, bytes(row.approval_hash).hex()


def runs(songenv: SongEnv, *job_ids: UUID) -> list[str]:
    rows = songenv.sql(
        "SELECT stage FROM agent_runs WHERE job_id = ANY(:ids) ORDER BY started_at",
        ids=list(job_ids),
    ).all()
    return [r.stage for r in rows]


def interpreted(songenv: SongEnv) -> list[Any]:
    return [s for p in songenv.providers for s in p.sent if s.spec.stage == "interpret"]


async def test_phase_one_reads_the_request_and_sizes_the_pick_by_it(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider = await songenv.rider()
    class_id, base = await stated(songenv, rider)
    seen: list[Any] = []
    real = revise_songs_mod.run_phase_one

    async def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["brief"])
        return await real(*args, **kwargs)

    monkeypatch.setattr(revise_songs_mod, "run_phase_one", spy)
    songenv.script = replace_script(reading=LONGER)
    answer = await change(rider, class_id, base)
    job_id = UUID(answer.json()["id"])
    await songenv.run(job_id)  # the recorded 30 minute pick no longer fits: only the plan matters

    [brief] = seen  # sized by the layered length, the request first in its text
    assert (brief.length_min, brief.explicit) == (25, ["length_min"])
    assert brief.text.startswith(REQUEST)
    [read] = interpreted(songenv)  # told the class's values, never the first request's text
    told = json.loads(read.spec.first_message)
    assert (told["current"]["length_min"], told["request"]) == (30, REQUEST)
    assert "brief" not in told
    # The run is the job's own, so it counts toward the job's cost ceiling and accounting.
    assert runs(songenv, job_id)[0] == "interpret"
    stored = songenv.sql("SELECT brief FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert stored["length_min"] == 30  # the class's own brief is unchanged


async def test_the_list_keeps_the_closed_reading_and_none_of_the_requests_words(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    class_id, base = await stated(songenv, rider)
    job_id, list_id, _ = await waiting(songenv, rider, class_id, base, READING)
    assert sorted(runs(songenv, job_id)) == ["choose_songs", "interpret", "pick_pool"]
    kept = songenv.revision(list_id).provenance
    assert kept["reading"] == {"games": [], "climbs": {"value": 1, "origin": "explicit"}}
    assert "A harder song" not in json.dumps(kept)


async def test_phase_two_layers_the_stored_reading_without_a_second_model_call(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    class_id, base = await stated(songenv, rider)
    job_id, list_id, digest = await waiting(songenv, rider, class_id, base, READING)
    assert (await rider.approve(job_id, list_id, digest)).status_code == 200
    songenv.script = short_script()  # its interpret turn reads nothing: it must not be used
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)

    assert runs(songenv, job_id).count("interpret") == 1  # phase 1's, never billed twice
    assert len(interpreted(songenv)) == 1
    [sent] = [s for p in songenv.providers for s in p.sent if s.spec.stage == "revise"]
    assert '"explicit":{"length_min":30,"climbs":1}' in sent.spec.first_message
    draft = songenv.revision(current(songenv, class_id))
    # The next revise starts from the layered values, as after a design revise.
    assert draft.provenance["stated"]["explicit"] == ["length_min", "climbs"]
    asked = {i["path"]: (i["origin"], i["value"]) for i in draft.provenance["interpretation"]}
    assert asked["climbs"] == ("explicit", 1)


async def test_a_resume_reuses_the_stored_reading(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base = await stated(songenv, rider)
    job_id, list_id, digest = await waiting(songenv, rider, class_id, base, READING)
    assert (await rider.approve(job_id, list_id, digest)).status_code == 200
    songenv.script = failing_script("auth")  # phase 2 fails at its first model call
    assert (await songenv.run(job_id)).error_category == "model_config"

    songenv.script = short_script()  # a resume: its interpret turn would read nothing
    answer = await resume_change(rider, class_id, base, list_id)
    assert answer.status_code == 201, answer.text
    resumed = UUID(answer.json()["id"])
    row = await songenv.run(resumed)
    assert (row.state, row.error_category) == ("completed", None)
    assert runs(songenv, resumed) == ["revise"]  # no interpret run: the user is not billed twice
    assert len(interpreted(songenv)) == 1
    draft = songenv.revision(current(songenv, class_id))
    assert draft.provenance["stated"]["explicit"] == ["length_min", "climbs"]


async def test_a_list_without_a_stored_reading_is_read_in_phase_two(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base = await stated(songenv, rider)
    job_id, list_id, digest = await waiting(songenv, rider, class_id, base, None)
    assert "reading" in songenv.revision(list_id).provenance  # an empty reading is still stored
    songenv.sql(  # a list made before this step existed
        "UPDATE class_revisions SET provenance = provenance - 'reading' WHERE id = :r", r=list_id
    )
    assert (await rider.approve(job_id, list_id, digest)).status_code == 200
    songenv.script = short_script(READING)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    assert runs(songenv, job_id).count("interpret") == 2  # phase 1's and this one
    draft = songenv.revision(current(songenv, class_id))
    assert draft.provenance["stated"]["explicit"] == ["length_min", "climbs"]


async def test_a_class_that_states_nothing_makes_no_interpret_run(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    job_id, list_id, _ = await waiting(songenv, rider, class_id, base, READING)
    assert runs(songenv, job_id) == ["pick_pool", "choose_songs"]
    assert "reading" not in songenv.revision(list_id).provenance


async def test_the_interpret_run_counts_toward_the_song_job_ceiling(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base = await stated(songenv, rider)
    songs.configure(SongConfig(enabled=True, sync_wait_seconds=0.3, cost_micros=1))
    songenv.script = replace_script(reading=READING)
    searches = sum(1 for s in songenv.api() if s.path == "/v1/search")
    answer = await change(rider, class_id, base)
    job_id = UUID(answer.json()["id"])
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "limit_exceeded")
    assert runs(songenv, job_id) == ["interpret"]  # stopped by the song ceiling, no pick paid
    assert sum(1 for s in songenv.api() if s.path == "/v1/search") == searches


def test_the_interpreter_gets_the_song_jobs_ceiling_and_the_design_limits() -> None:
    agent = AgentConfig(model="m", limits=Limits(max_turns=7, max_tool_calls=3, job_cost_micros=9))
    made = revise_reading._agent(agent, SongConfig(cost_micros=123))  # pyright: ignore[reportPrivateUsage]
    assert (made.limits.job_cost_micros, made.limits.max_turns, made.limits.max_tool_calls) == (
        123,
        7,
        3,
    )
