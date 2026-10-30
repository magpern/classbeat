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
- **The wait** (ADR 0005 sections 2 and 3, ADR 0008 section 7): the runner moves the job to
  ``waiting_for_approval`` in the same transaction as the revision and clears the lease, so the
  worker holds no slot for it and the per-user limit (``queued`` and ``running``) does not count
  it; nothing of it lives in memory, so a restart changes nothing. ``queue_expire`` ends it
  ``expired`` after ``CLASSBEAT_APPROVAL_TTL_HOURS`` with no model, MOWL or Spotify call (the
  pool playlist is left as it is), settle releases the credit once, and a later approval is
  refused ``expired``. A cancel while waiting is immediate. Approved, the job is ``queued`` with
  ``attempt = 0`` and the next claim runs the approved phase.
- **Phase 2** (``mode = "pick"`` after Approve; ``mode = "resume"``) is issue SJ2 (#181): until it
  lands, an approved job fails ``internal`` at its first claim (the credit is released) and
  ``admit`` refuses every resume. ``phase_two`` is the seam it replaces; it gets the run and the
  input and must end with ``Complete`` (a job waits at most once).

Registered in ``classbeat.jobs.KINDS`` on import, by the worker (which runs it) and the web app
(the jobs API validates with the same registry). The worker's runtime is ``design_draft``'s
(``classbeat.design.current_runtime``: the key ring, a new model provider per run, the agent
settings) plus ``classbeat.songs.config``; a run without a runtime fails ``internal``.
"""

from collections.abc import Awaitable, Callable
from typing import Final

from classbeat.design.kind import current_runtime, new_provider
from classbeat.jobs import KINDS, Complete, JobFailure, JobKind, JobRun, Outcome
from classbeat.songs import config
from classbeat.songs.admit import admit, approve_check
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.pick import run_phase_one

KIND: Final = "design_with_songs"
PHASE_ONE: Final = ("pool_search", "pool_write", "pool_analyse", "choose")
STAGES: Final = PHASE_ONE  # SJ2 adds phase 2's stages

PhaseTwo = Callable[[JobRun, DesignWithSongsInput], Awaitable[Complete]]


async def _not_built(run: JobRun, given: DesignWithSongsInput) -> Complete:
    raise JobFailure("internal")  # SJ2 (#181) builds phase 2


phase_two: PhaseTwo = _not_built


async def run(job_run: JobRun) -> Outcome:
    runtime = current_runtime()
    given = job_run.input
    if runtime is None or not isinstance(given, DesignWithSongsInput):
        raise JobFailure("internal")
    if job_run.approved:
        return await phase_two(job_run, given)
    if given.mode != "pick":  # a resume never waits; admit refuses it until SJ2
        raise JobFailure("internal")
    provider = new_provider(runtime)  # before any Spotify or MOWL call; this run's own
    return await run_phase_one(
        job_run,
        given,
        keyring=runtime.keyring,
        provider=provider,
        agent=runtime.agent,
        config=config.current(),
    )


DESIGN_WITH_SONGS: Final = KINDS.register(
    JobKind(
        KIND,
        DesignWithSongsInput,
        STAGES,
        run,
        consumes_credit=True,
        admit=admit,
        approve_check=approve_check,
    )
)
