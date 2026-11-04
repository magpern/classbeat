"""The ``refit_draft`` kind (ADR 0007 amendment "beat-one corrections", item 6, BC3).

Through the jobs API, the worker side, Postgres as ``classbeat_app`` and the fake MOWL; no model
is built, so a ``FakeProvider`` script is only used for the class's first design. Never the
network.
"""

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest

from classbeat.design.kind import DESIGN_DRAFT
from classbeat.design.refit import KIND, MAX_REFITS, REFIT_DRAFT
from classbeat.design.revise import REVISE_DRAFT
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from designtools import FakeMowlMusic
from jobapi import new_key
from optools import PLAYLIST
from revisetools import ReviseEnv

pytestmark = pytest.mark.usefixtures("jenv")


def seconds(content: dict[str, Any]) -> list[int]:
    return [i["sec"] for s in content["segments"] for i in s["intervals"]]


async def ask(renv: ReviseEnv, class_id: UUID, base: UUID) -> Any:
    body = {
        "kind": KIND,
        "input": {
            "class_id": str(class_id),
            "base_revision_id": str(base),
            "mowl_connection_id": str(renv.browser.connection_id),
        },
    }
    return await renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


async def run_refit(renv: ReviseEnv) -> None:
    settings = renv.denv.app.settings
    registry = KindRegistry()
    for kind in (DESIGN_DRAFT, REVISE_DRAFT, REFIT_DRAFT):
        registry.register(kind)
    row = await queue.claim(renv.denv.ops.factory, "bc3-w", settings.job_lease_seconds, [KIND])
    assert row is not None, "nothing to claim"
    await execute(
        Execution(row),
        renv.denv.ops.factory,
        registry,
        RunConfig(lease_seconds=settings.job_lease_seconds),
        asyncio.Event(),
    )


async def refit(renv: ReviseEnv, class_id: UUID, base: UUID) -> UUID:
    made = await ask(renv, class_id, base)
    assert made.status_code == 201, made.text
    await run_refit(renv)
    return UUID(made.json()["id"])


def correct_all(renv: ReviseEnv, class_id: UUID, beats: int = 2) -> list[dict[str, int]]:
    base = renv.revisions(class_id)[-1]
    out = []
    for song in base.music["songs"]:
        renv.sql(
            "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
            "VALUES (:o, :c, :f, :s, :b, :bpm)",
            o=renv.owner, c=class_id, f=base.music["fingerprint"], s=song["song"], b=beats,
            bpm=song["bpm"],
        )  # fmt: skip
        out.append({"song": song["song"], "beats": beats})
    return out


async def test_a_refit_stores_the_corrected_draft_as_the_current_child_without_a_model(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    applied = correct_all(renv, class_id)
    built = len(renv.providers)
    job_id = await refit(renv, class_id, base)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category, row.stage, row.credit_state) == (
        "completed", None, "fit", "none",
    )  # fmt: skip
    assert len(renv.providers) == built, "no model provider is built"
    assert renv.denv.agent_runs(job_id) == []
    first, second = renv.revisions(class_id)
    assert (second.number, second.parent_id, second.job_id) == (2, base, job_id)
    assert renv.current(class_id) == second.id
    assert seconds(first.content) != seconds(second.content), "changes moved to the new grid"
    assert sum(seconds(first.content)) == sum(seconds(second.content))
    assert [s["name"] for s in first.content["segments"]] == [
        s["name"] for s in second.content["segments"]
    ], "only the timing changes"
    assert second.music["beat_one"] == applied
    assert second.music["fingerprint"] == first.music["fingerprint"]
    assert second.provenance["origin"] == "beat_one"
    assert second.provenance["base_revision_id"] == str(base)
    for key in ("provider", "model", "agent_run_ids", "cost_micros", "usage"):
        assert key not in second.provenance
    assert renv.messages(class_id) == [], "a refit is not a chat revision"


async def test_a_refit_with_nothing_pending_is_refused_and_stores_nothing(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    made = await ask(renv, class_id, base)  # no corrections at all
    assert (made.status_code, made.json()["detail"]["reason"]) == (409, "nothing_pending")
    correct_all(renv, class_id)
    await refit(renv, class_id, base)
    current = renv.current(class_id)
    assert current is not None
    again = await ask(renv, class_id, current)  # the corrections are applied now
    assert (again.status_code, again.json()["detail"]["reason"]) == (409, "nothing_pending")
    assert len(renv.revisions(class_id)) == 2


@pytest.mark.parametrize(
    ("source", "reason"), [('{"source": "none"}', "invalid_input"), (None, "not_changeable")]
)
async def test_a_soundless_class_or_one_with_no_music_source_is_refused(
    renv: ReviseEnv, source: str | None, reason: str
) -> None:
    class_id, base = await renv.design()
    correct_all(renv, class_id)
    renv.sql(
        "UPDATE classes SET music_source = CAST(:s AS jsonb) WHERE id = :c", s=source, c=class_id
    )
    made = await ask(renv, class_id, base)
    assert (made.status_code, made.json()["detail"]["reason"]) == (409, reason)
    assert len(renv.revisions(class_id)) == 1


async def test_music_that_changed_since_the_base_ends_music_changed_and_stores_nothing(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, base = await renv.design()
    correct_all(renv, class_id)
    del fake_mowl.music.tracks[-1]  # the playlist changed since the base draft was made
    job_id = await refit(renv, class_id, base)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "music_changed")
    assert len(renv.revisions(class_id)) == 1
    assert renv.current(class_id) == base


async def test_admit_refuses_a_stale_base_a_foreign_one_a_busy_class_and_too_many_refits(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    _, other_base = await renv.design()
    foreign = await ask(renv, class_id, other_base)  # a revision of another class
    assert foreign.status_code == 409
    assert foreign.json()["detail"]["reason"] == "invalid_input"
    await renv.revise(class_id, base, "revise_good")
    correct_all(renv, class_id)  # rows for the current fingerprint, so a refit has work to do
    stale = await ask(renv, class_id, base)
    assert (stale.status_code, stale.json()["detail"]["reason"]) == (409, "stale_base")
    current = renv.current(class_id)
    assert current is not None
    first = await ask(renv, class_id, current)
    assert first.status_code == 201
    busy = await ask(renv, class_id, current)
    assert busy.status_code == 409, busy.text  # one open job per class
    await run_refit(renv)
    newest = renv.current(class_id)
    assert newest is not None
    for n in range(MAX_REFITS):
        renv.sql(
            "INSERT INTO class_revisions (owner_id, class_id, number, kind, content, "
            "content_hash, music, provenance, parent_id) SELECT owner_id, id, 100 + :n, 'draft', "
            "'{}'::jsonb, decode(md5(CAST(:n AS text)) || md5(CAST(:n AS text)), 'hex'), "
            "'{}'::jsonb, CAST(:p AS jsonb), NULL FROM classes WHERE id = :c",
            n=n, c=class_id, p=json.dumps({"origin": "beat_one"}),
        )  # fmt: skip
    limited = await ask(renv, class_id, newest)
    assert limited.status_code == 409
    detail = limited.json()["detail"]
    assert detail["reason"] == "refit_limit"
    assert detail["limit"] == MAX_REFITS


async def test_a_browser_cannot_send_the_base_hash(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    body = {
        "kind": KIND,
        "input": {
            "class_id": str(class_id),
            "base_revision_id": str(base),
            "mowl_connection_id": str(renv.browser.connection_id),
            "base_revision_hash": "0" * 64,
        },
    }
    made = await renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert made.status_code == 422


def correct(renv: ReviseEnv, class_id: UUID, song: int, beats: int, fingerprint: str) -> None:
    renv.sql(
        "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
        "VALUES (:o, :c, :f, :s, :b, 120)",
        o=renv.owner, c=class_id, f=fingerprint, s=song, b=beats,
    )  # fmt: skip


async def test_a_correction_that_moves_no_cut_still_stores_a_revision_recording_it(
    renv: ReviseEnv,
) -> None:
    """ADR item 6: stored even when the content is the base's again (song 1 one beat later moves
    no cut of the recorded design on the fake playlist)."""
    class_id, base = await renv.design()
    first = renv.revisions(class_id)[0]
    correct(renv, class_id, 1, 1, first.music["fingerprint"])
    job_id = await refit(renv, class_id, base)
    assert renv.denv.ops.row(job_id).state == "completed"
    _, second = renv.revisions(class_id)
    assert bytes(second.content_hash) == bytes(first.content_hash)
    assert second.content == first.content
    assert second.music["beat_one"] == [{"song": 1, "beats": 1}]
    assert renv.current(class_id) == second.id


async def test_a_base_no_longer_current_at_the_store_is_kept_but_not_made_current(
    renv: ReviseEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The revise compare-and-set (ADR item 6.5): the new revision is kept, and the class keeps
    the revision the user picked meanwhile."""
    from classbeat.design import refit as refit_mod

    class_id, base = await renv.design()
    correct_all(renv, class_id)
    real = refit_mod.refit

    def move_then_fit(*args: Any, **kwargs: Any) -> Any:
        renv.sql("UPDATE classes SET current_revision_id = NULL WHERE id = :c", c=class_id)
        return real(*args, **kwargs)

    monkeypatch.setattr(refit_mod, "refit", move_then_fit)
    job_id = await refit(renv, class_id, base)
    assert renv.denv.ops.row(job_id).state == "completed"
    _, second = renv.revisions(class_id)
    assert second.parent_id == base
    assert renv.current(class_id) is None


async def test_a_soundless_source_at_run_time_ends_invalid_input_and_stores_nothing(
    renv: ReviseEnv,
) -> None:
    """The run's backstop: admit saw a playlist class; its source is none when the job runs."""
    class_id, base = await renv.design()
    correct_all(renv, class_id)
    made = await ask(renv, class_id, base)
    assert made.status_code == 201, made.text
    renv.sql(
        'UPDATE classes SET music_source = \'{"source": "none"}\'::jsonb WHERE id = :c',
        c=class_id,
    )
    await run_refit(renv)
    row = renv.denv.ops.row(UUID(made.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert len(renv.revisions(class_id)) == 1


async def test_admit_counts_only_corrections_for_the_bases_music(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    correct(renv, class_id, 1, 2, "f" * 64)  # another fingerprint: not pending for this base
    made = await ask(renv, class_id, base)
    assert (made.status_code, made.json()["detail"]["reason"]) == (409, "nothing_pending")


async def test_a_base_whose_stored_hash_no_longer_holds_fails_internal(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    correct_all(renv, class_id)
    made = await ask(renv, class_id, base)
    assert made.status_code == 201, made.text
    renv.sql(  # as the superuser, past the immutability policy: the content no longer hashes
        "UPDATE class_revisions SET content = jsonb_set(content, '{name}', '\"tampered\"') "
        "WHERE id = :r",
        r=base,
    )
    await run_refit(renv)
    row = renv.denv.ops.row(UUID(made.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "internal")
    assert len(renv.revisions(class_id)) == 1


async def test_a_cancel_during_the_music_step_stores_nothing(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, base = await renv.design()
    correct_all(renv, class_id)
    made = await ask(renv, class_id, base)
    job_id = UUID(made.json()["id"])
    cancelled = False

    def cancel(path: str) -> None:
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            renv.sql("UPDATE jobs SET cancel_requested_at = now() WHERE id = :i", i=job_id)

    fake_mowl.on_read = cancel
    await run_refit(renv)
    assert renv.denv.ops.row(job_id).state == "cancelled"
    assert len(renv.revisions(class_id)) == 1
    assert renv.current(class_id) == base


def _running_gate(renv: ReviseEnv, flag: str | None = "on", *, covers: bool = True) -> None:
    """The running gate of #509: the environment's switch on, the web's ``fake`` covering (or
    not) and the admin flag stored ``flag`` (nothing stored is off)."""
    from classbeat.design import sport

    sport.configure(sport.SportGate(running_enabled=True, fake_covers=covers))
    renv.sql("DELETE FROM app_settings WHERE key = 'running_enabled'")
    if flag is not None:
        renv.sql(
            "INSERT INTO app_settings (key, value) VALUES ('running_enabled', CAST(:v AS jsonb))",
            v=json.dumps(flag),
        )


def _running_gate_off(renv: ReviseEnv) -> None:
    from classbeat.design import sport

    renv.sql("DELETE FROM app_settings WHERE key = 'running_enabled'")
    sport.configure(None)


async def _running_class(renv: ReviseEnv) -> tuple[UUID, UUID]:
    """A running class designed with the running gate on: (class id, its revision); the class's
    corrections cover every song, so a refit has work to do."""
    from classbeat import design

    renv.use("running_first_draft")
    body = {
        "kind": design.KIND,
        "input": {
            "title": "Tuesday run",
            "brief": {"sport": "running", "length_min": 30, "intensity": "moderate"},
            "music": {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST},
            "mowl_connection_id": str(renv.browser.connection_id),
        },
    }
    made = await renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert made.status_code == 201, made.text
    await renv.run(design.KIND)
    class_id = UUID(made.json()["class_id"])
    correct_all(renv, class_id)
    return class_id, renv.revisions(class_id)[0].id


async def test_a_running_class_is_refused_while_the_running_flag_is_off(renv: ReviseEnv) -> None:
    _running_gate(renv)
    try:
        class_id, base = await _running_class(renv)
        _running_gate(renv, "off")
        made = await ask(renv, class_id, base)
        assert (made.status_code, made.json()["detail"]["reason"]) == (409, "feature_off")
    finally:
        _running_gate_off(renv)


async def test_a_running_class_is_refused_while_its_plan_has_no_running_route(
    renv: ReviseEnv,
) -> None:
    """The whole running gate (#509) applies at admit, coverage included, though a refit calls no
    model: the same answer as a running revise of the class."""
    _running_gate(renv)
    try:
        class_id, base = await _running_class(renv)
        _running_gate(renv, covers=False)
        made = await ask(renv, class_id, base)
        assert (made.status_code, made.json()["detail"]["reason"]) == (
            409, "running_not_evaluated",
        )  # fmt: skip
    finally:
        _running_gate_off(renv)


async def test_a_queued_running_refit_whose_flag_went_off_ends_feature_off_calling_nothing(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    from classbeat.design import sport

    _running_gate(renv)
    try:
        class_id, base = await _running_class(renv)
        made = await ask(renv, class_id, base)
        assert made.status_code == 201, made.text
        reads = len(fake_mowl.reads)
        sport.configure(sport.SportGate(running_enabled=False, fake_covers=True))
        await run_refit(renv)
        row = renv.denv.ops.row(UUID(made.json()["id"]))
        assert (row.state, row.error_category) == ("failed", "feature_off")
        assert len(fake_mowl.reads) == reads, "nothing was called"
        assert len(renv.revisions(class_id)) == 1
    finally:
        _running_gate_off(renv)


async def test_a_running_class_is_refitted_with_the_running_check(renv: ReviseEnv) -> None:
    _running_gate(renv)
    try:
        class_id, base = await _running_class(renv)
        job_id = await refit(renv, class_id, base)
        assert renv.denv.ops.row(job_id).state == "completed"
        _, second = renv.revisions(class_id)
        assert second.provenance["sport"] == "running"
        assert "beat_one" in second.music
    finally:
        _running_gate_off(renv)
