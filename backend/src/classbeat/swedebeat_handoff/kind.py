"""The ``swedebeat_verify_mix`` job (ADR 0016 section 7, B2).

One press of "I made the mix" or "Check again". No credit. The server sets ``handoff_id``; a
request carries the class and at most a candidate mix id.

- **admit**: ``feature_off`` before the hand-off is read; then the MOWL connection; then
  ``invalid_input`` for a soundless class (``music_kind.refuse_soundless``, #530); then the
  stored match, which counts only when it is the current draft's song list; then a live,
  unexpired row for that match; then at most 10 jobs of this kind per user per hour. A verified
  row is not a job (the endpoint answers 200). Nothing is attached here.
- **run**: the flag again (a queued job ends ``feature_off`` with no request). An expired row
  ends ``handoff_expired`` before the connection is opened. A class soundless by now ends
  ``invalid_input`` (``music_kind.still_has_music``), before the listing and again before the
  attach (#470). The match is checked again. The
  user's mixes are listed. With no chosen mix, one new mix (not in the snapshot) is analysed;
  none is ``not_found``; several are remembered so the user can pick, and nothing is attached.
  A client mix id is analysed only when it is in that fresh listing. The analysis is §3's
  (``load_swedebeat_mix``: the same hosts, limits and daily slot). The length verdict is stored
  on the row; ``ok`` sets ``verified``. Recording it refuses ``invalid_input`` (nothing written)
  when the class's ``music_source`` is ``none`` by then (``music_kind.check_stored``).
"""

import logging
from typing import Annotated, Any, ClassVar, Final, Self
from uuid import UUID

from mowl import api as mowl_api
from pydantic import BaseModel, StringConstraints, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.classes.selection import stored_approval
from classbeat.connections import call_opened, find_in, open_connection
from classbeat.context import UserContext
from classbeat.design import music_kind
from classbeat.design.swedebeat_music import load_swedebeat_mix
from classbeat.flags import effective as flags
from classbeat.jobs import KINDS, Complete, JobFailure, JobKind, JobRun
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.operations.swedebeat import parse_mixes
from classbeat.swedebeat_handoff.config import CHECKS_PER_HOUR, current
from classbeat.swedebeat_handoff.rows import by_id, checks_this_hour, live, save_result
from classbeat.swedebeat_handoff.verdict import LengthVerdict, judge
from classbeat.swedebeat_match.revision import MatchRevision

logger = logging.getLogger(__name__)

KIND: Final = "swedebeat_verify_mix"
STAGES: Final = ("verify",)
MixId = Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-z_-]{1,64}$")]


class VerifyInput(JobInput):
    """A check: the class, the user's MOWL connection, and at most one candidate mix id."""

    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl",)

    mowl_connection_id: UUID
    mix_id: MixId | None = None
    handoff_id: UUID | None = None  # the server's; a browser is refused for sending it

    @model_validator(mode="after")
    def _names_its_class(self) -> Self:
        if self.class_id is None:
            raise ValueError("a verification names its class")
        return self

    @property
    def the_class(self) -> UUID:
        assert self.class_id is not None  # noqa: S101 - the validator above
        return self.class_id


async def _connected(db: AsyncSession, ctx: UserContext, connection_id: UUID) -> bool:
    if ctx.connection("mowl") != connection_id:
        return False
    info = await find_in(db, ctx, connection_id)
    return info is not None and info.provider == "mowl" and info.status == "connected"


async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel, /) -> Admitted | Refused:
    if not isinstance(parsed, VerifyInput):
        return Refused("invalid_input")
    if not await flags.swedebeat_enabled(db, current().enabled):
        return Refused("feature_off")
    if not await _connected(db, ctx, parsed.mowl_connection_id):
        return Refused("not_connected")
    soundless = await music_kind.refuse_soundless(db, ctx.user_id, parsed.the_class)  # #530
    if soundless is not None:
        return soundless
    bound, reason = await stored_approval(db, ctx.user_id, parsed.the_class)
    if bound is None:
        return Refused(reason or "stale_match")
    row = await live(db, ctx.user_id, parsed.the_class)
    if row is None:
        return Refused("not_found")
    if row.expired:
        return Refused("handoff_expired")
    if row.match_revision_id != bound.revision_id or row.state == "verified":
        return Refused("invalid_input")
    used, age = await checks_this_hour(db, ctx.user_id)
    if used >= CHECKS_PER_HOUR:
        return Refused(
            "rate_limited",
            {"limit": CHECKS_PER_HOUR, "used": used, "retry_after": max(1, 3600 - age)},
        )
    return Admitted({"handoff_id": row.id})


def _payload(candidates: list[str], verdict: LengthVerdict | None) -> dict[str, Any]:
    detail: dict[str, Any] = {"candidates": candidates}
    if verdict is not None:
        detail["analysed_sec"] = verdict.analysed_sec
        detail["expected_low_sec"] = verdict.expected_low_sec
        detail["expected_high_sec"] = verdict.expected_high_sec
        detail["missing_positions"] = list(verdict.missing_positions)
    return detail


async def _record(
    run: JobRun,
    handoff_id: UUID,
    match_id: UUID,
    *,
    state: str,
    verdict: str | None,
    mix_id: str | None,
    detail: dict[str, Any],
) -> None:
    """The check's result, or ``handoff_expired`` / ``invalid_input`` when the row may no
    longer take it. Nothing here calls MOWL."""
    async with run.fenced() as fs:
        row = await by_id(fs.db, run.lease.owner_id, handoff_id)
        if row is None or row.expired:
            raise JobFailure("handoff_expired")
        await music_kind.check_stored(fs, soundless=False)  # never for a soundless class (#530)
        bound, _reason = await stored_approval(fs.db, run.lease.owner_id, row.class_id)
        if bound is None or bound.revision_id != match_id:
            raise JobFailure("invalid_input")
        saved = await save_result(
            fs.db,
            run.lease.owner_id,
            handoff_id,
            state=state,
            verdict=verdict,
            mix_id=mix_id,
            detail=detail,
        )
        if not saved:
            raise JobFailure("handoff_expired")


async def _ready(
    run: JobRun, given: VerifyInput
) -> tuple[UUID, UUID, tuple[str, ...], MatchRevision]:
    """The flag, then the row and the match. ``feature_off`` and ``handoff_expired`` before any
    connection is opened."""
    settings = current()
    async with run.fenced() as fs:
        if not await flags.swedebeat_enabled(fs.db, settings.enabled):
            raise JobFailure("feature_off")
    if given.handoff_id is None or settings.keyring is None:
        raise JobFailure("internal")
    async with run.fenced() as fs:
        row = await by_id(fs.db, run.lease.owner_id, given.handoff_id)
        if row is None or row.class_id != given.the_class:
            raise JobFailure("invalid_input")
        if row.expired:
            raise JobFailure("handoff_expired")
        await music_kind.still_has_music(fs, given.the_class)  # before the listing (#470)
        bound, _reason = await stored_approval(fs.db, run.lease.owner_id, given.the_class)
        if bound is None or bound.revision_id != row.match_revision_id:
            raise JobFailure("invalid_input")
        return given.handoff_id, row.match_revision_id, row.mixes_before, bound.match


async def run(job_run: JobRun) -> Complete:
    given = job_run.input
    if not isinstance(given, VerifyInput):
        raise JobFailure("internal")
    await job_run.stage("verify")
    handoff_id, match_id, before, match = await _ready(job_run, given)
    if job_run.ctx.connection("mowl") != given.mowl_connection_id:
        raise JobFailure("not_connected")
    settings = current()
    assert settings.keyring is not None  # noqa: S101 - _ready refused a missing ring
    opened = await open_connection(job_run.factory, settings.keyring, job_run.ctx, "mowl")
    if opened.id != given.mowl_connection_id:
        raise JobFailure("not_connected")
    await job_run.checkpoint()
    listing = await call_opened(
        job_run.factory, job_run.ctx, opened, lambda creds: mowl_api.swedebeat_mixes(creds)
    )
    listed = parse_mixes(listing)
    ids = [entry.mix_id for entry in listed]
    known = set(before)
    candidates = [mix_id for mix_id in ids if mix_id not in known]
    chosen = given.mix_id
    if chosen is not None:
        if chosen not in ids:
            raise JobFailure("invalid_input")  # not this user's listing: nothing is attached
    elif len(candidates) == 1:
        chosen = candidates[0]
    elif not candidates:
        await _record(
            job_run,
            handoff_id,
            match_id,
            state="checked",
            verdict="not_found",
            mix_id=None,
            detail=_payload([], None),
        )
        logger.info("swedebeat mix verdict", extra={"verdict": "not_found"})
        return Complete()
    else:
        await _record(
            job_run,
            handoff_id,
            match_id,
            state="checked",
            verdict=None,
            mix_id=None,
            detail=_payload(candidates, None),
        )
        return Complete()
    async with job_run.fenced() as fs:
        row = await by_id(fs.db, job_run.lease.owner_id, handoff_id)
        if row is None or row.expired:
            raise JobFailure("handoff_expired")
        await music_kind.still_has_music(fs, given.the_class)  # before the attach (#470)
    analysis = await load_swedebeat_mix(
        job_run, settings.keyring, chosen, connection_id=given.mowl_connection_id
    )
    verdict = judge(match.content.songs, analysis.duration)
    await _record(
        job_run,
        handoff_id,
        match_id,
        state="verified" if verdict.verdict == "ok" else "checked",
        verdict=verdict.verdict,
        mix_id=chosen,
        detail=_payload(candidates, verdict),
    )
    logger.info("swedebeat mix verdict", extra={"verdict": verdict.verdict})
    return Complete()


VERIFY_MIX: Final = KINDS.register(
    JobKind(
        KIND,
        VerifyInput,
        STAGES,
        run,
        consumes_credit=False,
        admit=admit,
        server_fields=frozenset({"handoff_id"}),
    )
)
