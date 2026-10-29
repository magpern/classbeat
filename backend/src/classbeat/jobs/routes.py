"""``/api/jobs``: create a job, read it, cancel it, approve its song list (ADR 0005 section 2).

- ``POST /api/jobs`` (signed in; CSRF by the middleware) with an ``Idempotency-Key`` header (16
  to 64 of ``A-Za-z0-9_-``, made by the browser per user action) and ``{kind, input}``. The input
  is validated by the kind's model (a ``classbeat.jobs.inputs.JobInput``). 201 with the new job;
  the same key and body again is 200 with the same job; the same key with another body is 409
  ``idempotency_mismatch``. Also 409 ``class_busy``, ``not_connected``, ``quota``; 404
  ``class_not_found``; 429 ``busy`` (the per-user limit); 400 for the header; 422 for the body;
  413 ``too_large`` for a body over ``BODY_MAX_BYTES`` (also on approve). A kind with ``admit``
  (``classbeat.jobs.kinds``) may refuse with its own reason (409 ``{reason}`` and whole-number
  counts, unless the reason has a status above); an input naming one of the kind's server fields
  is 422 ``invalid_input``: only ``admit`` sets those.
- ``GET /api/jobs/{id}``: the job's state.
- ``GET /api/jobs/{id}/revision``: the draft the job stored, for the draft summary: 409
  ``no_revision`` while it has none. Answers only ``{revision_id, number, kind, content,
  unknown_bpm, created_at}``: ``content`` is re-read through ``WorkoutDraft`` (so a field that
  schema does not name is never shown), ``unknown_bpm`` is ``[{index, title}]`` (the user's own
  track titles, shown back to them). No provenance, link id, fingerprint or hash.
- ``POST /api/jobs/{id}/cancel``: 200 with the state (idempotent); 409 ``not_cancellable`` for a
  job that ended otherwise.
- ``POST /api/jobs/{id}/approve`` with ``{revision_id, content_hash}`` (64 hex characters): 200
  with the state (a repeated approval too); 409 with ``not_waiting``, ``expired``,
  ``superseded``, ``hash_mismatch``, ``not_connected``, ``invalid_input`` or ``quota``; 429
  ``busy``. Recent authentication is not required (ADR 0005 section 2).

Another user's job id is 404 ``not_found``, the same body as an unknown id or a malformed one.
Errors carry a reason code only: the create and approve bodies are validated here, and their
422 never echoes a submitted value, nor the name of a key the model does not know (#115 items 2
and 3); ``/api/jobs`` is also in ``SECRET_INPUT_PATHS``, so FastAPI's own 422 drops ``input``.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import auth
from classbeat.agent.schemas import WorkoutDraft
from classbeat.context import UserContext
from classbeat.jobs import service
from classbeat.jobs.credits import CreditLedger, UnmeteredLedger
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import KIND_NAME, KINDS, JobKind, KindRegistry, Refused
from classbeat.jobs.progress import read_revision
from classbeat.jobs.user import Admission, CreateAdmit, NewJob

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
INPUT_MAX_BYTES = 16384  # jobs.input's check (migration 0004), measured as jsonb prints it
# A request body of the jobs API is read only up to this (#115 item 8; an app-wide cap is M6's).
BODY_MAX_BYTES = 65536

_STATUS = {"not_found": 404, "class_not_found": 404, "busy": 429, "too_large": 422}


def job_kinds() -> KindRegistry:
    """The kinds the API accepts: the worker's registry (tests override this dependency)."""
    return KINDS


def credit_ledger() -> CreditLedger:
    """The credit seam (ADR 0005 section 5); M5 replaces it."""
    return UnmeteredLedger()


class CreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern=KIND_NAME.pattern)
    input: dict[str, Any]


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


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
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None

    @classmethod
    def of(cls, view: service.JobView) -> "JobOut":
        return cls.model_validate(view, from_attributes=True)


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
    reason: str, status: int | None = None, counts: Mapping[str, int] | None = None
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


def canonical_json(value: Any) -> bytes:
    """The canonical JSON of ADR 0004 section 2 (sorted keys, no spaces, UTF-8): the stored
    input and the request hash. Not imported from ``classbeat.crypto`` (guarded there)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _jsonb_size(value: Any) -> int:
    # jsonb's text form separates with ", " and ": "; the database check stays the backstop.
    return len(json.dumps(value, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8"))


def _bound_admit(kind: JobKind, parsed: BaseModel) -> CreateAdmit | None:
    """The kind's ``admit`` for this input, run by ``user.create`` under the owner lock. What it
    accepts is validated by the kind's model again and stored in place of the browser's input."""
    admit = kind.admit
    if admit is None:
        return None

    async def run(db: AsyncSession, ctx: UserContext) -> Admission | Refused:
        result = await admit(db, ctx, parsed)
        if isinstance(result, Refused):
            return result
        stored = kind.admitted_input(parsed, result.fields).model_dump(mode="json")
        if _jsonb_size(stored) > INPUT_MAX_BYTES:
            return Refused("too_large")
        return Admission(canonical_json(stored).decode("utf-8"), result.after_insert)

    return run


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
    if kind is None or not issubclass(kind.input_model, JobInput):
        return _refuse("unknown_kind", 422)
    if kind.server_fields & body.input.keys():
        return _invalid()  # set by the server only; never echoed
    try:
        parsed = kind.parse_input(body.input)
    except ValidationError as error:
        return _invalid(error)
    assert isinstance(parsed, JobInput)  # noqa: S101 - parse_input returns input_model
    stored = parsed.model_dump(mode="json")
    if _jsonb_size(stored) > INPUT_MAX_BYTES:
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
        admit=_bound_admit(kind, parsed),
    )
    settings = request.app.state.settings
    result = await service.create(
        request.app.state.session_factory,
        ctx,
        new,
        limit=settings.jobs_per_user,
        ledger=ledger,
    )
    if result.job is None:
        return _refuse(result.reason or "invalid_input", counts=result.counts)
    out = JobOut.of(result.job)
    if not result.created:
        return JSONResponse(out.model_dump(mode="json"), status_code=200)
    return out


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
    return _refuse("not_found") if view is None else JobOut.of(view)


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
        draft = WorkoutDraft.model_validate(stored.content)
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
        limit=request.app.state.settings.jobs_per_user,
        ledger=ledger,
    )
    if result.job is None:
        return _refuse(result.reason or "not_found")
    if not result.accepted:
        return _refuse(result.reason or "not_waiting")
    return JobOut.of(result.job)
