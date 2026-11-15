"""The ``send_to_player`` job kind (ADR 0034 section 5): the user's press of "Send to player" for
one revision.

Input ``{class_id, revision_id, content_hash}`` (``SendToPlayerInput``): ids and the revision's
stored hash (lower-case hex), never a document or a number. ``consumes_credit = False``: no
model, no ledger. The revision and its hash are bound to the press: every check below is made
against that revision, and what is sent is its document, never a later revision's.

Revalidation at the three points, each failing closed:

1. **``admit``** (the create transaction): the class and revision are the user's own and of a
   kind Save accepts, with the sent hash equal to the stored and recomputed one
   (``saving.approval.revision``); ``player_format_policy`` passes (its code is the refusal);
   no open job of the class (the jobs API: ``class_busy``); no player operation of the class is
   ``uncertain`` (``unresolved_write``); the ``player`` connection is ``connected``
   (``not_connected``).
2. **``prepare``**: the player connection's token read; then one fenced transaction with the
   same checks from the database, the connection held, the document converted here and its
   ``document_hash`` taken, and the attempt's publication number planned (a retry keeps its own).
3. **The decide step**: ``classbeat.player_send.publish`` (the hold, then the number taken under
   the publication row's lock).
"""

from typing import Annotated, ClassVar, Final, Self
from uuid import UUID

from pydantic import BaseModel, StringConstraints, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections.player_account import (
    PlayerLink,
    hold_player_link,
    player_connected,
    read_player_link,
)
from classbeat.context import UserContext
from classbeat.jobs import KINDS, Complete, JobFailure, JobKind, JobRun
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.operations import orphans
from classbeat.player_format_policy import Converted, Refusal
from classbeat.player_publish_policy import SEND_KIND, operation_key
from classbeat.player_send import publications
from classbeat.player_send.document import document_of
from classbeat.player_send.operation import send_context
from classbeat.player_send.publish import Publication, publish
from classbeat.player_send.runtime import current_runtime
from classbeat.saving import approval

KIND: Final = "send_to_player"
STAGES: Final = ("prepare", "send")

ContentHash = Annotated[str, StringConstraints(pattern=approval.HEX_SHA256)]


class SendToPlayerInput(JobInput):
    """A press of "Send to player": the class, the revision and its content hash."""

    CONNECTIONS: ClassVar[tuple[str, ...]] = ()

    revision_id: UUID
    content_hash: ContentHash

    @model_validator(mode="after")
    def _names_its_class(self) -> Self:
        if self.class_id is None:
            raise ValueError("a send names its class")
        return self

    @property
    def the_class(self) -> UUID:
        assert self.class_id is not None  # noqa: S101 - the validator above
        return self.class_id

    @property
    def digest(self) -> bytes:
        return bytes.fromhex(self.content_hash)


async def _checked(
    db: AsyncSession, ctx: UserContext, given: SendToPlayerInput
) -> tuple[approval.Revision, Converted] | str:
    """The revision and its document, or the refusal's code."""
    found = await approval.revision(db, ctx, given.the_class, given.revision_id, given.digest)
    if isinstance(found, approval.Refusal):
        return found.reason
    converted = await document_of(db, ctx.user_id, found)
    if isinstance(converted, Refusal):
        return converted.code
    return found, converted


async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel) -> Admitted | Refused:
    """Point 1, inside the create transaction; nothing is stored on a refusal."""
    if not isinstance(parsed, SendToPlayerInput):
        return Refused("invalid_input")
    checked = await _checked(db, ctx, parsed)
    if isinstance(checked, str):
        return Refused(checked)
    if await orphans.player_unresolved(db, ctx, parsed.the_class):
        return Refused("unresolved_write")
    if not await player_connected(db, ctx.user_id):
        return Refused("not_connected")
    return Admitted()


_JOB_CLASS = text("SELECT class_id FROM jobs WHERE id = :job_id AND owner_id = :owner_id")


async def prepare(run: JobRun, link: PlayerLink, given: SendToPlayerInput) -> Publication:
    """Point 2: the publication this attempt makes."""
    lease = run.lease
    async with run.fenced() as fs:
        # Its own transaction, so the marking stays when the send is refused below.
        await orphans.resolve(fs.db, run.ctx)
    async with run.fenced() as fs:
        job_class = (await fs.db.execute(_JOB_CLASS, lease.params())).scalar_one_or_none()
        if job_class != given.the_class:
            raise JobFailure("internal")
        if await orphans.player_unresolved(fs.db, run.ctx, given.the_class, lease.job_id):
            raise JobFailure("uncertain_write")
        if not await hold_player_link(fs.db, lease.owner_id, link):
            raise JobFailure("not_connected")
        checked = await _checked(fs.db, run.ctx, given)
        if isinstance(checked, str):
            raise JobFailure("invalid_input")
        revision, converted = checked
        planned = await publications.planned_number(
            fs.db, lease.owner_id, given.the_class, lease.job_id
        )
    if planned.document_hash not in (None, converted.content_hash):
        raise JobFailure("invalid_input")  # a retry never sends another document
    number = planned.number
    key = operation_key(given.the_class, number)

    async def take(db: AsyncSession) -> bool:
        return await publications.take_number(db, lease.owner_id, given.the_class, number, key)

    return Publication(
        kind=SEND_KIND,
        source_class_id=given.the_class,
        number=number,
        document=converted.document,
        document_hash=converted.content_hash,
        class_id=given.the_class,
        context=send_context(given.the_class, number, revision.revision_id, converted.content_hash),
        take=take,
    )


async def run(job_run: JobRun) -> Complete:
    runtime = current_runtime()
    given = job_run.input
    if runtime is None or not isinstance(given, SendToPlayerInput) or job_run.approved:
        raise JobFailure("internal")
    await job_run.stage("prepare")
    link = await read_player_link(job_run.factory, runtime.keyring, job_run.ctx)
    if link is None or link.token is None:
        raise JobFailure("not_connected")
    publication = await prepare(job_run, link, given)
    await job_run.stage("send")
    await publish(job_run, runtime, link, publication)
    return Complete()


SEND_TO_PLAYER: Final = KINDS.register(
    JobKind(KIND, SendToPlayerInput, STAGES, run, consumes_credit=False, admit=admit)
)
