"""``/api/jobs``: create a job, read it, cancel it, approve its song list (ADR 0005 section 2).

- ``POST /api/jobs`` (signed in; CSRF by the middleware) with an ``Idempotency-Key`` header (16
  to 64 of ``A-Za-z0-9_-``, made by the browser per user action) and ``{kind, input}``. The input
  is validated by the kind's model (a ``classbeat.jobs.inputs.JobInput``). 201 with the new job;
  the same key and body again is 200 with the same job; the same key with another body is 409
  ``idempotency_mismatch``. Also 409 ``class_busy``, ``not_connected``, ``quota``; 404
  ``class_not_found``; 429 ``busy`` (the per-user limit); 400 for the header; 422 for the body;
  ``quota`` (ADR 0011 section 9, FE1's contract) is ``{reason: "quota", quota: "classes" |
  "cost", limit, used, period_end, plan}`` (``limit`` and ``used`` for ``classes`` only, a cost
  refusal shows no money; ``period_end`` the ISO date the next period starts; Save and Update add
  ``saves`` with ``limit``, ``used`` and ``window``, #577) and ``busy``
  carries the plan's ``limit`` (also on approve);
  413 ``too_large`` for a body over ``BODY_MAX_BYTES`` (also on approve; the app-wide
  ``bodylimit.BodyLimit`` is equal and answers first, as ``body_too_large``). A kind with ``admit``
  (``classbeat.jobs.kinds``) may refuse with its own reason (409 ``{reason}`` and whole-number
  counts, unless the reason has a status above); an input naming one of the kind's server fields
  is 422 ``invalid_input``: only ``admit`` sets those. So is one naming a field every input of the
  kind's model leaves to the server (``JobInput.SERVER_FIELDS``: a model job's ``route_key``, set at
  creation, ADR 0012 section 5).
- ``GET /api/jobs/{id}``: the job's state.
- ``GET /api/jobs/{id}/revision``: the draft the job stored, for the draft summary: 409
  ``no_revision`` while it has none. Answers only ``{revision_id, number, kind, content,
  unknown_bpm, created_at}``: ``content`` is re-read through ``WorkoutDraft`` (``RunningDraft``
  for a running class, ADR 0018; so a field that schema does not name is never shown),
  ``unknown_bpm`` is ``[{index, title}]`` (the user's own track titles, shown back to them). No
  provenance, link id, fingerprint or hash.
- ``POST /api/jobs/{id}/cancel``: 200 with the state (idempotent); 409 ``not_cancellable`` for a
  job that ended otherwise.
- ``POST /api/jobs/{id}/approve`` with ``{revision_id, content_hash}`` (64 hex characters): 200
  with the state (a repeated approval too); 409 with ``not_waiting``, ``expired``,
  ``superseded``, ``hash_mismatch``, ``not_connected``, ``invalid_input``, ``quota`` or the
  kind's own reason (``approve_check``: ``account_changed`` for ``design_with_songs``); 429
  ``busy``; 404 ``not_found`` also for a ``revision_id`` that is not one of the user's own song
  lists of the job's class. Recent authentication is not required (ADR 0005 section 2).

Another user's job id is 404 ``not_found``, the same body as an unknown id or a malformed one.
Errors carry a reason code only: the create and approve bodies are validated here, and their
422 never echoes a submitted value, nor the name of a key the model does not know (#115 items 2
and 3); ``/api/jobs`` is also in ``SECRET_INPUT_PATHS``, so FastAPI's own 422 drops ``input``.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from classbeat import auth
from classbeat.agent.stored_draft import stored_draft
from classbeat.context import UserContext
from classbeat.jobs import service
from classbeat.jobs.bound_admit import INPUT_MAX_BYTES, bound_admit
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.hold import CreateHold
from classbeat.jobs.http import (
    IDEMPOTENCY_KEY,
    Enqueued,
    Factory,
    canonical_json,
    credit_ledger,
    job_limit,
)
from classbeat.jobs.inputs import JobInput, jsonb_size
from classbeat.jobs.kinds import KIND_NAME, KINDS, JobKind, KindRegistry
from classbeat.jobs.progress import read_revision
from classbeat.jobs.sentences import job_sentence_of
from classbeat.jobs.user import JobLimitPolicy, NewJob
from classbeat.usage import job_usage

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# A request body of the jobs API is read only up to this (#115 item 8). The app-wide cap
# (``classbeat.bodylimit``, #351) is equal and answers first; this is the second line.
BODY_MAX_BYTES = 65536

_STATUS = {
    "not_found": 404,
    "class_not_found": 404,
    "busy": 429,
    "too_large": 422,
    "handoff_expired": 410,
}


def job_kinds() -> KindRegistry:
    """The kinds the API accepts: the worker's registry (tests override this dependency)."""
    return KINDS


class CreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern=KIND_NAME.pattern)
    input: dict[str, Any]


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class JobUsageOut(BaseModel):
    runs: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_micros: int


class JobOut(BaseModel):
    id: UUID
    kind: str
    state: str
    stage: str | None
    class_id: UUID
    cancel_requested: bool
    approval_revision_id: UUID | None
    expires_at: datetime | None
    approved_at: datetime | None
    error_category: str | None
    error_message: str | None = None  # a song-picking, Spotify or Swedebeat-wait sentence
    # The same sentence as a catalogue code and its numbers (ADR 0023 section 6); the page words
    # the code and shows ``error_message`` for a code it does not know.
    sentence: str | None = None
    sentence_params: dict[str, int] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    usage: JobUsageOut | None = None  # GET only: summed over the job's runs; None if none

    @classmethod
    def of(cls, view: service.JobView) -> "JobOut":
        out = cls.model_validate(view, from_attributes=True)
        found = job_sentence_of(
            view.state,
            view.error_category,
            view.spotify_failure,
            view.spotify_retry_after,
            view.available_at,
            datetime.now(UTC),
            view.kind,
        )
        if found is not None:
            out.error_message = found.text
            out.sentence = found.code
            out.sentence_params = found.params
        return out


class UnknownBpmOut(BaseModel):
    index: int
    title: str


class RevisionOut(BaseModel):
    revision_id: UUID
    number: int
    kind: str
    content: dict[str, Any]
    unknown_bpm: list[UnknownBpmOut]
    created_at: datetime


def _refuse(
    reason: str, status: int | None = None, counts: Mapping[str, int | str] | None = None
) -> JSONResponse:
    return JSONResponse(
        {"detail": {**(counts or {}), "reason": reason}},
        status_code=status or _STATUS.get(reason, 409),
    )


def _loc(error: Any) -> list[Any]:
    """Where an error is; for an extra key, its parent only (the key is the client's text)."""
    loc = list(error["loc"])
    return loc[:-1] if error["type"] == "extra_forbidden" else loc


def _invalid(error: ValidationError | None = None) -> JSONResponse:
    """422 without the submitted values (``input``, ``ctx`` and ``url`` are left out)."""
    errors = (
        []
        if error is None
        else [
            {"loc": _loc(e), "type": e["type"]}
            for e in error.errors(include_url=False, include_context=False, include_input=False)
        ]
    )
    return JSONResponse({"detail": {"reason": "invalid_input", "errors": errors}}, status_code=422)


class _TooLarge(Exception):
    pass


async def _body(request: Request) -> bytes:
    """The request body, at most ``BODY_MAX_BYTES`` (read in chunks, never more than that)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > BODY_MAX_BYTES:
        raise _TooLarge
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > BODY_MAX_BYTES:
            raise _TooLarge
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "",
    status_code=201,
    response_model=JobOut,
    responses={200: {"model": JobOut}, 400: {}, 404: {}, 409: {}, 413: {}, 422: {}, 429: {}},
)
async def create_job(
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    kinds: Annotated[KindRegistry, Depends(job_kinds)],
    ledger: Annotated[CreditLedger, Depends(credit_ledger)],
    limit: Annotated[JobLimitPolicy, Depends(job_limit)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JobOut | JSONResponse:
    if idempotency_key is None:
        return _refuse("idempotency_key_missing", 400)
    if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        return _refuse("idempotency_key_invalid", 400)
    try:
        body = CreateIn.model_validate_json(await _body(request))
    except _TooLarge:
        return _refuse("too_large", 413)
    except ValidationError as error:
        return _invalid(error)
    kind = kinds.get(body.kind)
    model = None if kind is None else kind.input_model
    if kind is None or model is None or not issubclass(model, JobInput):
        return _refuse("unknown_kind", 422)
    if (kind.server_fields | model.SERVER_FIELDS) & body.input.keys():
        return _invalid()  # set by the server only; never echoed
    try:
        parsed = kind.parse_input(body.input)
    except ValidationError as error:
        return _invalid(error)
    assert isinstance(parsed, JobInput)  # noqa: S101 - parse_input returns input_model
    stored = parsed.model_dump(mode="json")
    if jsonb_size(stored) > INPUT_MAX_BYTES:
        return _refuse("too_large")
    new = NewJob(
        kind=kind.name,
        input=canonical_json(stored).decode("utf-8"),
        class_id=parsed.class_id,
        title=parsed.title,
        connection_ids=parsed.connection_ids(),
        idempotency_key=idempotency_key,
        request_hash=hashlib.sha256(canonical_json({"kind": kind.name, "input": stored})).digest(),
        consumes_credit=kind.consumes_credit,
        admit=bound_admit(kind, parsed),
    )
    result = await service.create(
        request.app.state.session_factory, ctx, new, limit=limit, ledger=ledger
    )
    if result.job is None:
        return _refuse(result.reason or "invalid_input", counts=result.counts)
    out = JobOut.of(result.job)
    if not result.created:
        return JSONResponse(out.model_dump(mode="json"), status_code=200)
    return out


async def enqueue(
    factory: Factory,
    ctx: UserContext,
    parsed: JobInput,
    kind: JobKind,
    *,
    idempotency_key: str,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    consumes_credit: bool,
    hold: CreateHold | None = None,
) -> Enqueued:
    """Create one job for the authenticated user through ``service.create`` (with ``hold``:
    ``service.create_held``, the caller's reads and refusal in the same transaction).

    The hand-off routes call this. It is the same owner lock, credit ledger and ``admit`` as
    ``POST /api/jobs``. ``admit`` refuses a stored input over 65536 bytes, the ceiling the
    hand-off used; this module's own create uses ``INPUT_MAX_BYTES``.
    """
    stored = parsed.model_dump(mode="json")
    new = NewJob(
        kind=kind.name,
        input=canonical_json(stored).decode("utf-8"),
        class_id=parsed.class_id,
        title=None,
        connection_ids=parsed.connection_ids(),
        idempotency_key=idempotency_key,
        request_hash=hashlib.sha256(canonical_json({"input": stored, "kind": kind.name})).digest(),
        consumes_credit=consumes_credit,
        admit=bound_admit(kind, parsed, max_bytes=65536),
    )
    if hold is None:
        result = await service.create(factory, ctx, new, limit=limit, ledger=ledger)
    else:
        result = await service.create_held(factory, ctx, new, limit=limit, ledger=ledger, hold=hold)
    if result.job is None:
        return Enqueued(None, False, result.reason, result.counts)
    return Enqueued(result.job.id, result.created, state=result.job.state)


def _job_id(raw: str) -> UUID | None:
    """The path's job id; ``None`` for a malformed one (answered like an unknown id)."""
    try:
        return UUID(raw)
    except ValueError:
        return None


@router.get("/{job_id}", response_model=JobOut, responses={404: {}})
async def get_job(
    job_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> JobOut | JSONResponse:
    wanted = _job_id(job_id)
    if wanted is None:
        return _refuse("not_found")
    view = await service.get(request.app.state.session_factory, ctx, wanted)
    if view is None:
        return _refuse("not_found")
    out = JobOut.of(view)
    used = await job_usage(request.app.state.session_factory, ctx, wanted)
    if used is not None:
        out.usage = JobUsageOut(**asdict(used))
    return out


@router.get("/{job_id}/revision", response_model=RevisionOut, responses={404: {}, 409: {}})
async def get_revision(
    job_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> RevisionOut | JSONResponse:
    wanted = _job_id(job_id)
    if wanted is None:
        return _refuse("not_found")  # a malformed id is the same 404 as an unknown one
    found = await read_revision(request.app.state.session_factory, ctx, wanted)
    if found is None:
        return _refuse("not_found")
    stored = found.revision
    if stored is None:
        return _refuse("no_revision")
    try:
        draft = stored_draft(stored.content, stored.sport)  # the class's sport's schema
        raw: Any = stored.music.get("unknown_bpm", [])
        unknown = [UnknownBpmOut(index=int(u["index"]), title=str(u["title"])) for u in raw]
    except (ValidationError, KeyError, TypeError, ValueError):
        return JSONResponse({"detail": {"reason": "internal"}}, status_code=500)
    return RevisionOut(
        revision_id=stored.id,
        number=stored.number,
        kind=stored.kind,
        content=draft.model_dump(mode="json", exclude_none=True),
        unknown_bpm=unknown,
        created_at=stored.created_at,
    )


@router.post("/{job_id}/cancel", response_model=JobOut, responses={404: {}, 409: {}})
async def cancel_job(
    job_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> JobOut | JSONResponse:
    wanted = _job_id(job_id)
    if wanted is None:
        return _refuse("not_found")
    view = await service.cancel(request.app.state.session_factory, ctx, wanted)
    if view is None:
        return _refuse("not_found")
    if view.state in ("completed", "failed", "expired"):
        return _refuse("not_cancellable")
    return JobOut.of(view)


@router.post(
    "/{job_id}/approve",
    response_model=JobOut,
    responses={404: {}, 409: {}, 413: {}, 422: {}, 429: {}},
)
async def approve_job(
    job_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    kinds: Annotated[KindRegistry, Depends(job_kinds)],
    ledger: Annotated[CreditLedger, Depends(credit_ledger)],
    limit: Annotated[JobLimitPolicy, Depends(job_limit)],
) -> JobOut | JSONResponse:
    wanted = _job_id(job_id)
    if wanted is None:
        return _refuse("not_found")
    try:
        body = ApproveIn.model_validate_json(await _body(request))
    except _TooLarge:
        return _refuse("too_large", 413)
    except ValidationError as error:
        return _invalid(error)
    result = await service.approve(
        request.app.state.session_factory,
        ctx,
        wanted,
        body.revision_id,
        bytes.fromhex(body.content_hash),
        kinds=kinds,
        limit=limit,
        ledger=ledger,
    )
    if result.job is None:
        return _refuse(result.reason or "not_found", counts=result.counts)
    if not result.accepted:
        return _refuse(result.reason or "not_waiting", counts=result.counts)
    return JobOut.of(result.job)
