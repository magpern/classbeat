"""#1353 (ADR 0040 section 8 and its amendment, item 4): **every failure kind ends a studio track
analysis in its category**, from one table (``spotdownkinds``): each ``UnreachableReason``, each
job status, a rate limit over the budget, a spent budget, a document of other bytes, a refused map
and an error in a step. A kind that is not a map ends the job; nothing is stored; every
``Unreachable`` site logs ``outage_policy.log_message``; and an error of any type is ``internal``
and never swallowed.

Also: there is no local fallback on the design path (the local analysis is gone, #1357: no design
module names ``analyse_audio``); and a retried ending gives the next attempt
a fresh wait budget and outage flag (a requeue that is not a retry keeps what was spent).
"""

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from classbeat.design import track_spotdown
from classbeat.spotdown import outage_policy, studio_tracks_config
from classbeat.spotdown.studio_tracks_config import TracksConfig
from jobtools import new_job
from spotdownfake import FakeSpotdown, spotdown
from spotdownkinds import REASONS, all_kinds, apply
from spotdownstudioharness import ONE, TWO, Harness, env, harness, work

__all__ = ["env", "harness", "spotdown", "work"]  # the fixtures

pytestmark = pytest.mark.usefixtures("spotdown_slot_by_default")
KEY = bytes(range(32))
UPLOAD_ON = TracksConfig(KEY)


@pytest.fixture(autouse=True)
def installed(monkeypatch: pytest.MonkeyPatch) -> Any:
    async def pause(*_args: object) -> None:
        return None

    monkeypatch.setattr(track_spotdown, "pause", pause)
    studio_tracks_config.configure(UPLOAD_ON)
    yield
    studio_tracks_config.configure(None)


def stored(harness: Harness, owner: Any) -> int:
    return harness.ops.sql(
        "SELECT count(*) FROM swedebeat_track_maps WHERE owner_id = :o", o=owner
    ).scalar_one()


@pytest.mark.parametrize(("kind", "category"), all_kinds())
async def test_each_failure_kind_ends_the_job_in_its_category(
    harness: Harness,
    spotdown: FakeSpotdown,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    work: Path,
    kind: str,
    category: str,
) -> None:
    config = apply(
        kind,
        spotdown,
        monkeypatch,
        unmappable=(track_spotdown, "valid_track_map", lambda *_a: False),
        broken=(track_spotdown, "studio_map"),
    )
    if config == "not_configured":
        studio_tracks_config.configure(None)
    owner = await harness.user()
    with caplog.at_level(logging.WARNING):
        row = await harness.run(owner, [ONE, TWO])
    assert (row.state, row.error_category) == ("failed", category)
    assert stored(harness, owner) == 0
    assert not any(work.iterdir())  # the audio is deleted on every way out  # noqa: ASYNC240
    if kind.startswith("unreachable:"):  # every Unreachable site logs the fixed line
        reason = kind.removeprefix("unreachable:")
        assert REASONS[reason] == category
        assert f"category={category} reason={reason}" in caplog.text
        assert outage_policy.log_message(reason) in caplog.text  # pyright: ignore[reportArgumentType]
    assert "trk-" not in caplog.text  # no reference in what is logged


# --- the local analysis is gone ------------------------------------------------------------------


async def test_there_is_no_local_fallback_on_the_design_path(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    owner = await harness.user()
    row = await harness.run(owner, [ONE, TWO])
    assert (row.state, row.error_category) == ("completed", None)
    spotdown.connect_error = True  # and on a failure no local fallback is tried either
    other = await harness.user()
    failed = await harness.run(other, [ONE])
    assert (failed.state, failed.error_category) == ("failed", "analysis_unavailable")


def test_no_design_module_names_the_local_analysis() -> None:
    import classbeat.design as design

    root = Path(design.__file__).parent
    assert not (root / "mixanalysis.py").exists()  # #1357: the files are gone
    assert not (root / "mixchild.py").exists()
    named = [p.name for p in root.glob("*.py") if "analyse_audio" in p.read_text(encoding="utf-8")]
    assert named == []


# --- a retried ending gives the next attempt a fresh budget --------------------------------------


def job(harness: Harness, owner: Any, references: list[str]) -> Any:
    body = json.dumps({"references": references})
    return new_job(harness.ops.admin, owner, input=body, max_attempts=2)


def budgets(harness: Harness, owner: Any) -> list[Any]:
    return harness.ops.sql(
        "SELECT started_at, outage FROM spotdown_studio_budgets WHERE owner_id = :o", o=owner
    ).all()


async def test_a_retried_attempt_starts_with_a_fresh_wait_budget_and_outage_flag(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    owner = await harness.user()
    job_id = job(harness, owner, [ONE])
    harness.ops.sql(  # attempt 1 finds the job's 600 s spent and the outage flagged
        "INSERT INTO spotdown_studio_budgets (owner_id, job_id, started_at, outage) "
        "VALUES (:o, :j, now() - interval '11 minutes', true)",
        o=owner, j=job_id,
    )  # fmt: skip
    await harness.ops.run_next()
    first = harness.ops.row(job_id)
    assert (first.state, first.error_category) == ("queued", None)  # retried
    assert spotdown.seen == []  # attempt 1 called nothing
    assert budgets(harness, owner) == []  # the retry reset the budget and the flag
    harness.ops.sql("UPDATE jobs SET available_at = now() WHERE id = :j", j=job_id)
    await harness.ops.run_next()
    second = harness.ops.row(job_id)
    assert (second.state, second.error_category) == ("completed", None)
    assert spotdown.seen  # attempt 2 called spotdown: its budget was fresh, its flag clear
    assert [b.outage for b in budgets(harness, owner)] == [False]


async def test_a_non_retried_ending_keeps_the_spent_budget(
    harness: Harness, spotdown: FakeSpotdown
) -> None:
    """``analysis_failed`` is not retried: nothing resets (the job is over)."""
    owner = await harness.user()
    spotdown.end = "alignment_failed"
    job_id = job(harness, owner, [ONE])
    await harness.ops.run_next()
    row = harness.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "analysis_failed")
    assert len(budgets(harness, owner)) == 1
