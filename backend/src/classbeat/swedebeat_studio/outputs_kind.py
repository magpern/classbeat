"""The ``produce_outputs`` job kind, **a stub until SR C7** (ADR 0036 section 6, SR C6a).

C6a registers the kind so the consent route's approval is created, bound and re-checked by the
real path; C7 replaces ``run`` with the outputs themselves. Until then a run:

1. re-checks the approval from the database in one fenced read (ADR 0007 section 5, point 2):
   ``outputs_admit.bound`` again (the studio on, the class's current studio draft with the
   approved hash, the choice still allowed here, the SwedeBeat connection the list's, live and
   share-locked, the Spotify account for ``spotify_playlist``) **and** the generation stored at the
   approval. A failure ends the job: ``feature_off`` (the studio or an output turned off),
   ``invalid_input`` (the draft is no longer the approved current one, or the choice no longer
   valid), ``not_connected`` (another, a reconnected or a gone connection: ADR 0007's category
   for a reconnect in between; C7 brings ``connection_changed`` as a job category with its
   migration), ``account_changed`` or ``internal`` (a stored hash that no longer holds);
2. then ends ``feature_off`` with the operator line ``produce_outputs not built``: **nothing is
   sent and nothing is stored**, and the job is terminal, so no later worker can run it.

It runs no model, consumes no credit and makes no gateway, Spotify or MOWL call.
"""

import logging
from typing import Final

from classbeat.jobs import KINDS, Complete, JobFailure, JobKind, JobRun
from classbeat.swedebeat_studio.outputs_admit import KIND, SERVER_FIELDS, admit, bound
from classbeat.swedebeat_studio.outputs_input import ProduceOutputsInput

logger = logging.getLogger(__name__)

STAGES: Final = ("check",)
_AS_JOB: Final = {
    "feature_off": "feature_off",
    "output_off": "feature_off",
    "not_connected": "not_connected",
    "connection_changed": "not_connected",
    "account_changed": "account_changed",
    "internal": "internal",
}


async def run(job_run: JobRun) -> Complete:
    given = job_run.input
    if not isinstance(given, ProduceOutputsInput) or job_run.approved:
        raise JobFailure("internal")
    if given.swedebeat_generation is None:
        raise JobFailure("internal")  # admit always stores it
    await job_run.stage("check")
    async with job_run.fenced() as fs:
        done = await bound(fs.db, fs.lease.ctx, given, given.swedebeat_generation, lock=False)
    if isinstance(done, str):
        raise JobFailure(_AS_JOB.get(done, "invalid_input"))
    logger.info("produce_outputs not built", extra={"category": "feature_off"})
    raise JobFailure("feature_off")


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
