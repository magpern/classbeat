"""A studio class's outputs and the user's actions on one (ADR 0036 section 6, ADR 0005 section 7;
SR C7; ``classes.studio_output_actions``).

- ``GET /api/classes/{class_id}/outputs``: ``{outputs: [{revision_id, output, attempt, state,
  actions, note, name?, category?, superseded?, candidate?}]}``, the newest attempt per draft and
  output, its state derived from its operations. No key, reference, provider id, URL or body.
- ``POST /api/classes/{class_id}/outputs/{output}/check-again`` ``{revision_id}``: reconcile that
  output's attempt once more (a mix in doubt: look it up in MOWL by its unique name, shown only).
- ``POST .../swedebeat_mix/confirm`` ``{revision_id}``: the user confirms the shown entry is
  their mix.
- ``POST .../create-another`` ``{revision_id, acknowledge_duplicate: true}``: "Create another
  anyway" for an output in doubt (the next approval opens ``attempt + 1`` with new keys).

Signed in (``auth.require_user``), CSRF-checked by the middleware, and each ``POST`` needs an
``Idempotency-Key`` (``jobs.http.IDEMPOTENCY_KEY``); every action is idempotent by its target, so
a repeated key and body answers the same. 404 ``not_found`` for another user's or an unknown
class, an unknown output and with the studio off; 409 ``{reason}`` for ``not_in_doubt`` and
``nothing_to_confirm``; 400 for a missing or bad key; 422 for a bad body.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from classbeat import auth
from classbeat.classes import studio_output_actions as outputs
from classbeat.classes.studio_songs_routes import _uuid  # pyright: ignore[reportPrivateUsage]
from classbeat.context import UserContext
from classbeat.jobs.http import IDEMPOTENCY_KEY
from classbeat.studio.output_choice_policy import OUTPUTS

router = APIRouter(prefix="/api/classes", tags=["classes"])

_STATUS = {
    "not_found": 404,
    "feature_off": 404,
    "invalid_input": 422,
    "idempotency_key_missing": 400,
    "idempotency_key_invalid": 400,
}


class RevisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: str = Field(max_length=64)


class AnotherIn(RevisionIn):
    acknowledge_duplicate: Literal[True]


def _refuse(reason: str) -> JSONResponse:
    body = {"reason": "not_found" if reason == "feature_off" else reason}
    return JSONResponse({"detail": body}, status_code=_STATUS.get(reason, 409))


def _answer(done: dict[str, Any] | str) -> JSONResponse:
    return _refuse(done) if isinstance(done, str) else JSONResponse(done)


async def _parsed(request: Request, model: type[RevisionIn], key: str | None) -> RevisionIn | str:
    if key is None:
        return "idempotency_key_missing"
    if not IDEMPOTENCY_KEY.fullmatch(key):
        return "idempotency_key_invalid"
    try:
        return model.model_validate_json(await request.body())
    except ValidationError:
        return "invalid_input"


@router.get("/{class_id}/outputs", responses={404: {}})
async def list_outputs(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found")
    return _answer(await outputs.view(request.app.state.session_factory, ctx, wanted))


@router.post(
    "/{class_id}/outputs/{output}/{action}",
    responses={400: {}, 404: {}, 409: {}, 422: {}},
)
async def act_on_output(
    class_id: str,
    output: str,
    action: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    wanted = _uuid(class_id)
    allowed = action in ("check-again", "create-another") or (
        action == "confirm" and output == "swedebeat_mix"
    )
    if wanted is None or output not in OUTPUTS or not allowed:
        return _refuse("not_found")
    model = AnotherIn if action == "create-another" else RevisionIn
    body = await _parsed(request, model, idempotency_key)
    if isinstance(body, str):
        return _refuse(body)
    revision = _uuid(body.revision_id)
    if revision is None:
        return _refuse("invalid_input")
    factory = request.app.state.session_factory
    if action == "check-again":
        keyring = request.app.state.keyring
        done = await outputs.check_again(factory, keyring, ctx, wanted, revision, output)
    elif action == "confirm":
        done = await outputs.confirm(factory, ctx, wanted, revision)
    else:
        done = await outputs.create_another(factory, ctx, wanted, revision, output)
    return _answer(done)
