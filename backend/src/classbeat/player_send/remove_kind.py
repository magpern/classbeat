"""The ``player_remove`` job kind (ADR 0034 section 5): the removal a class's deletion enqueues
(``publications.on_class_delete``), never a user's or a model's request.

Its input ``{source_class_id, number}`` is no ``JobInput``, so the jobs API refuses to create it
(``unknown_kind``): only the class delete's transaction inserts one, with ``class_id`` NULL (the
only kind the jobs table allows that for). The class is gone; the job is the publication row's.

``prepare`` reads the player connection's token. The decide step holds the connection and
checks that the publication row is still removed with this number; then the removal (``removed:
true``, no document) goes through the same protocol and outcome mapping as a send.
"""

from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections.player_account import read_player_link
from classbeat.jobs import KINDS, Complete, JobFailure, JobKind, JobRun
from classbeat.player_publish_policy import NUMBER_LIMIT, REMOVE_KIND
from classbeat.player_send import publications
from classbeat.player_send.operation import remove_context
from classbeat.player_send.publish import Publication, publish
from classbeat.player_send.runtime import current_runtime

KIND: Final = publications.REMOVE_JOB_KIND
STAGES: Final = ("remove",)


class PlayerRemoveInput(BaseModel):
    """Stored by the class delete: the deleted class's id and the removal's number."""

    model_config = ConfigDict(extra="forbid")

    source_class_id: UUID
    number: int = Field(gt=0, lt=NUMBER_LIMIT)


async def run(job_run: JobRun) -> Complete:
    runtime = current_runtime()
    given = job_run.input
    if runtime is None or not isinstance(given, PlayerRemoveInput) or job_run.approved:
        raise JobFailure("internal")
    await job_run.stage("remove")
    link = await read_player_link(job_run.factory, runtime.keyring, job_run.ctx)
    if link is None or link.token is None:
        raise JobFailure("not_connected")
    owner_id, class_id, number = job_run.lease.owner_id, given.source_class_id, given.number

    async def still(db: AsyncSession) -> bool:
        return await publications.removal_still(db, owner_id, class_id, number)

    publication = Publication(
        kind=REMOVE_KIND,
        source_class_id=class_id,
        number=number,
        document=None,
        document_hash=None,
        class_id=None,
        context=remove_context(class_id, number),
        take=still,
    )
    await publish(job_run, runtime, link, publication)
    return Complete()


PLAYER_REMOVE: Final = KINDS.register(
    JobKind(KIND, PlayerRemoveInput, STAGES, run, consumes_credit=False)
)
