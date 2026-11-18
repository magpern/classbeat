"""The ``design_with_songs`` job kind (ADR 0008 sections 4, 5 and 7; issue SJ1 for phase 1).

- **Input** ``DesignWithSongsInput`` (``classbeat.songs.inputs``), ``CONNECTIONS = ("mowl",
  "spotify")``: the jobs API checks both at creation and again at approval.
- **Credit**: ``consumes_credit = True`` (the class's credit, as ``design_draft``). It is reserved
  at creation, released by settle when the job ends anything but ``completed`` (an expired or
  cancelled wait included), and committed only with a draft (phase 2).
- **admit** and **approve_check** (``classbeat.songs.admit``): ``song_picking_off``,
  ``consent_missing``, ``pool_busy``, the seed rule; at approval the re-hashed list and
  ``account_changed``.
- **Phase 1** (``classbeat.songs.pick``): stages ``pool_search``, ``pool_write``,
  ``pool_analyse``, ``choose``; it ends with ``AwaitApproval`` and the ``song_list`` revision.
  With ``source`` (the user's own Spotify playlist, roadmap Amendment 26) it is
  ``classbeat.songs.from_playlist`` instead: ``pool_analyse`` of that playlist only (read, never
  written), the list in its order, no model; then the same wait and the same phase 2.
- **The wait** (ADR 0005 sections 2 and 3, ADR 0008 section 7): the runner moves the job to
  ``waiting_for_approval`` in the same transaction as the revision and clears the lease, so the
  worker holds no slot for it and the per-user limit (``queued`` and ``running``) does not count
  it; nothing of it lives in memory, so a restart changes nothing. ``queue_expire`` ends it
  ``expired`` after ``CLASSBEAT_APPROVAL_TTL_HOURS`` with no model, MOWL or Spotify call (the
  pool playlist is left as it is), settle releases the credit once, and a later approval is
  refused ``expired``. A cancel while waiting is immediate. Approved, the job is ``queued`` with
  ``attempt = 0`` and the next claim runs the approved phase.
- **Phase 2** (``classbeat.songs.phase2``, issue SJ2): the approved job (``mode = "pick"`` after
  Approve) or a resume (``mode = "resume"``, a new job admitted against a receipt, which never
  waits) runs ``prepare``, ``create``, ``fill``, ``attach``, ``music`` and ``design`` and ends
  with ``Complete``: the final private playlist, verified, analysed in MOWL, and the class's
  first design (a job waits at most once). ``phase_two`` is the seam the tests replace.

- **The composer's modes** (ADR 0038 sections 5 and 6, #1188): ``fill``, ``swap`` and ``change``
  (``classbeat.songs.composer``) end phase 1 with a ``v: 3`` list whose ``preview`` is the class
  designed on its songs; their fields are server fields, set only by the hand-offs, which create
  them with no credit. The approved job, and a composer resume (the hand-off
  ``classes.song_resume``, no credit), run ``classbeat.songs.composer_phase2``: the final playlist,
  then ``fit`` instead of ``design``, the first real draft and ``save_pending`` (#1189). Only a
  composer list's approval may carry ``save: true`` (``saves_on_approval``).

Registered in ``classbeat.jobs.KINDS`` on import, by the worker (which runs it) and the web app
(the jobs API validates with the same registry). The worker's runtime is ``design_draft``'s
(``classbeat.design.current_runtime``: the key ring, the model of each stage group from the job's
``route_key``, the agent settings) plus ``classbeat.songs.config``; a run without a runtime fails
``internal``.
"""

from collections.abc import Awaitable, Callable
from typing import Final

from pydantic import BaseModel

from classbeat.design.kind import Runtime, current_runtime, group_model
from classbeat.jobs import KINDS, Complete, JobFailure, JobKind, JobRun, Outcome
from classbeat.quota.route import with_plan, with_plan_run
from classbeat.songs import config
from classbeat.songs.admit import admit, approve_check
from classbeat.songs.composer import run_composer
from classbeat.songs.composer_phase2 import run_composer_phase_two
from classbeat.songs.from_playlist import run_source_phase_one
from classbeat.songs.inputs import COMPOSER_FIELDS, DesignWithSongsInput
from classbeat.songs.phase2 import PHASE_TWO, run_phase_two
from classbeat.songs.pick import SeedSpec, run_phase_one

KIND: Final = "design_with_songs"
PHASE_ONE: Final = ("pool_search", "pool_write", "pool_analyse", "choose")
STAGES: Final = (*PHASE_ONE, *PHASE_TWO, "fit")  # ``fit``: a composer's phase 2 (ADR 0038)

PhaseTwo = Callable[[JobRun, DesignWithSongsInput], Awaitable[Complete]]
ComposerPhaseTwo = Callable[[JobRun, DesignWithSongsInput, Runtime], Awaitable[Complete]]


async def _phase_two(run: JobRun, given: DesignWithSongsInput) -> Complete:
    runtime = current_runtime()
    if runtime is None:
        raise JobFailure("internal")
    # Phase 2 runs the ``design`` group, before any Spotify or MOWL call; its own provider.
    model = await group_model(runtime, run, given.route_key, "design")
    return await run_phase_two(
        run,
        given,
        keyring=runtime.keyring,
        provider=model.provider,
        agent=model.agent,
        config=config.current(),
    )


phase_two: PhaseTwo = _phase_two
composer_phase_two: ComposerPhaseTwo = run_composer_phase_two  # a seam the tests replace


def saves_on_approval(parsed: BaseModel) -> bool:
    """An approval with ``save: true`` is a composer list's only (ADR 0038 section 7)."""
    return isinstance(parsed, DesignWithSongsInput) and parsed.composer


async def run(job_run: JobRun) -> Outcome:
    runtime = current_runtime()
    given = job_run.input
    if runtime is None or not isinstance(given, DesignWithSongsInput):
        raise JobFailure("internal")
    if (job_run.approved and given.composer) or given.composer_resume:
        # ADR 0038 section 7: the approved composer list's phase 2 (``fit``, ``save_pending``)
        return await composer_phase_two(job_run, given, runtime)
    if given.composer:  # ADR 0038: fill, swap and change (``classbeat.songs.composer``)
        return await run_composer(job_run, given, runtime)
    if job_run.approved or given.mode == "resume":
        if job_run.approved and given.mode != "pick":
            raise JobFailure("internal")  # a resume never waits
        return await phase_two(job_run, given)
    if given.source is not None:  # the user's own playlist: no model, no pool (Amendment 26)
        return await run_source_phase_one(job_run, given, given.source, keyring=runtime.keyring)
    # Phase 1 runs the ``songs`` group, before any Spotify or MOWL call; its own provider.
    model = await group_model(runtime, job_run, given.route_key, "songs")
    return await run_phase_one(
        job_run,
        given,
        brief=given.brief,
        seed=SeedSpec.of(given.seed),
        origin=KIND,
        keyring=runtime.keyring,
        provider=model.provider,
        agent=model.agent,
        config=config.current(),
    )


DESIGN_WITH_SONGS: Final = KINDS.register(
    JobKind(
        KIND,
        DesignWithSongsInput,
        STAGES,
        with_plan_run(run),  # the current plan's gates for an input without any (#496)
        consumes_credit=True,
        admit=with_plan(admit),
        approve_check=approve_check,
        saves_on_approval=saves_on_approval,
        server_fields=frozenset({"route_key", "plan_gates", *COMPOSER_FIELDS}),
    )
)
