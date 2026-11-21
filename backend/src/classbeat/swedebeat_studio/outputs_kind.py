"""The ``produce_outputs`` job (ADR 0036 sections 6 and 7, ADR 0005 section 7, SR C7): the outputs
the user chose on the consent screen for **one** approved studio draft, each with its own state.

0. **The binding version**: a job whose input lacks ``binding_version`` 2 (approved while
   ``produce_outputs`` was C6a's stub) or has another ends ``invalid_input`` before anything.
1. **Check** (one fenced read; ADR 0007 section 5, point 2): ``outputs_admit.bound`` again (the
   studio and each chosen output on, the class's current studio draft with the approved hash, the
   SwedeBeat connection the list's, live and share-locked **at the approved generation**, the
   Spotify account for ``spotify_playlist``). A failure ends the job with nothing written:
   ``feature_off``, ``invalid_input``, ``not_connected``, ``connection_changed`` (another or a
   reconnected SwedeBeat connection), ``account_changed``, ``playlist_has_repeats`` (the
   SwedeBeat playlist for a draft that repeats a song, ADR 0036 amendment A8) or ``internal``.
   Then the bearer of that connection (its generation again) and the user's analyses of the
   draft's tracks.
2. **Each chosen output**, in ``output_choice_policy.OUTPUTS`` order, on its current attempt
   (``outputs_attempt.current``: opened with its names and plan before any write intent; nothing
   for a ``done`` output or one **in doubt**, which blocks every new write to it):
   ``spotify_playlist`` (``outputs_spotify``), ``swedebeat_playlist`` and ``swedebeat_mix``
   (``outputs_swedebeat``), ``mowl`` (``outputs_mowl``, only once the mix is ``done``). Every
   external call is an ADR 0005 external operation keyed by the approval
   (``out:<class>:<revision>:<output>:<attempt>:<step>``), every returned id is stored before the
   next step, and a resumed output continues from its stored id. **One output's failure never
   stops another**: each is settled (``outputs_attempt.settle``) and the next one runs.
3. **The end**: ``Complete`` when no output failed; otherwise the job fails with one category,
   a retryable one first (the retry resumes every open output and skips the rest), then
   ``uncertain_write`` (an output in doubt, for the user's Check again or Create another anyway),
   then the first.

It consumes no credit and runs no model. Every SwedeBeat write is made only because this user
chose that output for that class (ADR 0036 section 7). Only categories and counts are logged.
"""

import logging
from collections.abc import Callable, Mapping
from typing import Any, Final

from sqlalchemy import text

from classbeat.design.studio_revision_model import wanted
from classbeat.design.track_cache import cached, recorded
from classbeat.jobs import KINDS, Complete, JobFailure, JobKind, JobRun
from classbeat.jobs.errors import RETRYABLE
from classbeat.studio import output_body_policy as body
from classbeat.swedebeat_match import config as match_config
from classbeat.swedebeat_match.kind import open_match_bearer
from classbeat.swedebeat_studio import outputs_mowl, outputs_rows, outputs_spotify
from classbeat.swedebeat_studio import outputs_swedebeat as swedebeat
from classbeat.swedebeat_studio.outputs_admit import KIND, SERVER_FIELDS, admit, bound
from classbeat.swedebeat_studio.outputs_attempt import current, settle
from classbeat.swedebeat_studio.outputs_input import BINDING_VERSION, ProduceOutputsInput
from classbeat.swedebeat_studio.outputs_job import Producer
from classbeat.swedebeat_studio.outputs_rows import OutputRow

logger = logging.getLogger(__name__)

STAGES: Final = ("check", "spotify_playlist", "swedebeat_playlist", "swedebeat_mix", "mowl")
_AS_JOB: Final = {
    "feature_off": "feature_off",
    "output_off": "feature_off",
    "not_connected": "not_connected",
    "connection_changed": "connection_changed",
    "account_changed": "account_changed",
    "playlist_has_repeats": "playlist_has_repeats",
    "internal": "internal",
}
_TITLE = text("SELECT title FROM classes WHERE owner_id = :owner_id AND id = :class_id")
_STEPS: Final = {
    "spotify_playlist": outputs_spotify.spotify_playlist,
    "swedebeat_playlist": swedebeat.playlist,
    "swedebeat_mix": swedebeat.mix,
    "mowl": outputs_mowl.attach,
}


def _needs_maps(p: Producer) -> None:
    if any(r not in p.maps for r in p.references):
        raise JobFailure("music_changed")  # an analysis is gone: Change songs analyses again


def _detail(p: Producer, output: str, mix: OutputRow | None) -> Callable[[], Mapping[str, Any]]:
    """What the attempt stores before its first write intent (``output_body_policy``)."""
    spec = p.draft.spec

    def made() -> Mapping[str, Any]:
        if output == "spotify_playlist":
            return {"name": body.class_name(p.title, 100)}
        if output == "mowl":
            assert mix is not None  # noqa: S101 - ``_produce`` checked it
            return {
                "mix_url": mix.detail.get("mix_url"),
                "duration_sec": mix.detail.get("duration_sec"),
                "mix_attempt": mix.attempt,
            }
        _needs_maps(p)
        facts, durations = p.facts(), p.durations()
        if output == "swedebeat_playlist":
            name = body.class_name(p.title)
            return {"name": name, "body": body.playlist_body(name, spec, facts, durations)}
        plan = body.render_plan(spec, durations)
        last = plan[-1]
        return {
            "name": body.mix_name(p.title, body.new_suffix()),
            "plan": plan,
            "sha256s": p.sha256s(),
            "x_tracks": body.x_tracks(plan, facts),
            "duration_sec": (last["dst_start"] + last["src_end"] - last["src_start"]) / body.RATE,
        }

    return made


async def _mix_done(p: Producer) -> OutputRow | None:
    async with p.run.fenced() as fs:
        row = await outputs_rows.latest(
            fs.db, p.owner_id, p.class_id, p.given.revision_id, "swedebeat_mix"
        )
    return row if row is not None and row.state == "done" and row.detail.get("mix_url") else None


async def _produce(p: Producer, output: str) -> None:
    mix = None
    if output == "mowl":
        mix = await _mix_done(p)
        if mix is None:
            return  # waits for the mix: its own state says why
    row = await current(p, output, _detail(p, output, mix))
    if row is None:
        return
    failure: JobFailure | None = None
    try:
        await _STEPS[output](p, row)
    except JobFailure as failed:
        failure = failed
    state = await settle(p, row, failure)
    logger.info("studio output settled", extra={"output": output, "state": state})
    if failure is not None:
        raise failure


def _ending(failures: list[JobFailure]) -> JobFailure:
    for failure in failures:
        if failure.category in RETRYABLE:
            return failure
    for failure in failures:
        if failure.category == "uncertain_write":
            return failure
    return failures[0]


async def _producer(job_run: JobRun, given: ProduceOutputsInput) -> Producer:
    generation = given.swedebeat_generation
    async with job_run.fenced() as fs:
        done = await bound(fs.db, fs.lease.ctx, given, generation, lock=False)
        title = (
            await fs.db.execute(
                _TITLE, {"owner_id": fs.lease.owner_id, "class_id": given.the_class}
            )
        ).scalar_one_or_none()
    if isinstance(done, str):
        raise JobFailure(_AS_JOB.get(done, "invalid_input"))
    cfg = match_config.current()
    bearer = await open_match_bearer(job_run, cfg, given.swedebeat_connection_id)
    if bearer.connection.generation != generation:
        raise JobFailure("connection_changed")
    if cfg.keyring is None or cfg.swedebeat_gateway_key is None:  # checked by the open
        raise JobFailure("internal")
    refs = done.music.mix_spec.references()  # exactly the analyses the draft was designed on
    maps = await cached(job_run, bearer.connection, refs, recorded(wanted(done.music, refs)))
    return Producer(
        job_run, cfg.keyring, given, done, bearer, cfg.swedebeat_gateway_key, title, maps
    )


async def run(job_run: JobRun) -> Complete:
    given = job_run.input
    if not isinstance(given, ProduceOutputsInput) or job_run.approved:
        raise JobFailure("internal")
    if given.binding_version != BINDING_VERSION:
        raise JobFailure("invalid_input")  # approved for another version (the C6a stub's)
    if given.swedebeat_generation is None:
        raise JobFailure("internal")  # admit always stores it
    await job_run.stage("check")
    p = await _producer(job_run, given)
    failures: list[JobFailure] = []
    for output in given.outputs:
        await job_run.stage(output)
        try:
            await _produce(p, output)
        except JobFailure as failed:
            failures.append(failed)
    if failures:
        raise _ending(failures)
    return Complete()


PRODUCE_OUTPUTS: Final = KINDS.register(
    JobKind(
        KIND,
        ProduceOutputsInput,
        STAGES,
        run,
        consumes_credit=False,
        admit=admit,
        server_fields=SERVER_FIELDS,
    )
)
