"""Duplicate a class (ADR 0007 amendment of 2026-11-03, part D; ADR 0011 amendment of the same
date; issue #547).

``POST /api/classes/{id}/duplicate {revision_id, content_hash, title}`` makes a **new class** of the
signed-in user whose revision 1 is a copy of one draft of their own class. One ``user_session``,
every statement scoped by ``owner_id``:

0. the whole transaction runs under the owner advisory lock (``jobs.user.lock_owner``), taken
   first, as job creation does; then the ``Idempotency-Key`` (the jobs API's convention): the same
   key and request again answer 200 with the class made before, the same key with another request
   is 409 ``idempotency_mismatch`` (the key and the request hash live in the copy's
   ``provenance.duplicated_from``, so a retry never takes a second credit);
1. the source class and the draft are the user's own (a foreign, unknown or malformed id, and a
   revision that is not a draft of that class, are the same 404 ``not_found``); the sent hash must
   be the stored one (409 ``hash_mismatch``), which must still be the SHA-256 of the canonical
   content; a draft with a game MOWL no longer offers is 409 ``game_retired`` (ADR 0029); the
   content must validate under the class's sport (500 ``internal`` otherwise); a class with no
   stored brief is 409 ``not_ready``; a SwedeBeat studio class (its source or the draft's music;
   ADR 0036, whose hash covers the mix spec) is 409 ``studio_not_duplicable``;
2. (``classbeat.classes.duplicate_gates``) a running class is admitted only by
   ``design.sport.admit_running``, as a running design is
   (``feature_off``, or ``running_not_evaluated`` for the user's uncovered plan; ADR 0018 Amendment
   5, #509), and a class or draft on a Swedebeat mix while ``swedebeat_enabled`` is off (the
   class's current source **or** the chosen revision's music on a mix: the stricter rule, on
   purpose) is 409 ``feature_off``; a soundless class (``{source:
   "none"}``, #526) has no gate, as a soundless design has none (#1543: "No music" is always a
   choice), and its copy stays ``none`` (source and revision music);
3. the user's **current** plan: the brief and the copied draft (409 ``quota`` ``length`` or
   ``feature``), then one class of the month (``MeteredLedger.take_class``, 409 ``quota``
   ``classes``). Every refusal raises or returns before anything is written, and a raised refusal
   rolls the transaction back.

What is written: the class (title, the brief byte for byte, the music source only as the user's
own playlist or mix choice, no dismissed rules) and its revision 1 (content and hash copied, no
parent, no job, the music without the source class's ``song_list_id``, a provenance with
``origin: "duplicate"`` and ``duplicated_from`` and the source revision's nearest stated layer, if
any (``stated``; ADR 0029 section 4, #907), no run, usage or cost). Nothing else of the source
is read or copied: no saved workout, operation, job, message, song list, Swedebeat match or
hand-off, playlist or usage row. No MOWL, Spotify, Swedebeat or model request is made; ownership
of the music is re-checked by the paths that use it (revise, Save).

Beat-one corrections (DU3, #585; the beat-one amendment's item 8 is the text of record): the copy's
revision 1 keeps ``music.beat_one``, and after it is stored the same transaction copies the source
class's ``beat_one_corrections`` rows for the chosen revision's ``music.fingerprint`` to the copy
(same fingerprint, song, beats and BPM; a new id; the user's ``owner_id``; the copy's class), so
the copy's corrections read as applied, not pending, and Apply does not strip them. Rows for other
fingerprints are not copied, and neither are any rows when the copy has no ``music_source`` (a
class on picked songs, which cannot be refitted) or is soundless. At most ``MAX_PER_CLASS`` rows
(the table's cap for one fingerprint) are copied, lowest song first; the source cannot hold more.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import auth, quota
from classbeat.agent.beat_one import MAX_PER_CLASS
from classbeat.classes.duplicate_gates import flag_refusal
from classbeat.classes.duplicate_music import (
    HASH,
    corrections_fingerprint,
    music_copy,
    music_source_copy,
)
from classbeat.classes.duplicate_origin import described
from classbeat.classes.duplicate_provenance import provenance_copy
from classbeat.classes.duplicate_request import clean_title as clean_title
from classbeat.classes.duplicate_request import request_digest
from classbeat.classes.duplicate_sql import (
    ADOPT,
    BY_KEY,
    COPY_CORRECTIONS,
    NEW_CLASS,
    NEW_REVISION,
    SOURCE,
)
from classbeat.config import Settings
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design import chat
from classbeat.design.games_policy import RETIRED, has_retired_game
from classbeat.design.music_kind import is_studio
from classbeat.design.revision_hash import revision_hash
from classbeat.design.revision_ready import revision_ready
from classbeat.jobs.credits import QuotaRefused
from classbeat.jobs.http import IDEMPOTENCY_KEY
from classbeat.jobs.user import lock_owner
from classbeat.quota.gates import check_brief, check_content, current_gates
from classbeat.saving.approval import spec_of, sport_of_brief

Factory = async_sessionmaker[AsyncSession]

router = APIRouter(prefix="/api/classes", tags=["classes"])

STUDIO: Final = "studio_not_duplicable"  # a SwedeBeat studio class (ADR 0036)


class DuplicateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: UUID
    content_hash: str = Field(max_length=64)
    title: str = Field(max_length=400)


class DuplicateOut(BaseModel):
    class_id: UUID
    revision_id: UUID


@dataclass(frozen=True, slots=True)
class Outcome:
    """``reason`` is ``None`` when the class was made (``class_id`` and ``revision_id`` set)."""

    reason: str | None = None
    class_id: UUID | None = None
    revision_id: UUID | None = None
    replay: bool = False  # an earlier request with the same key made this class


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


async def duplicate(
    factory: Factory,
    settings: Settings,
    ledger: quota.MeteredLedger,
    ctx: UserContext,
    class_id: UUID,
    revision_id: UUID,
    content_hash: bytes,
    title: str,
    idempotency_key: str,
) -> Outcome:
    """The module docstring's transaction. ``QuotaRefused`` propagates (409 ``quota``)."""
    require_context(ctx)
    ids = {"owner_id": ctx.user_id, "class_id": class_id, "revision_id": revision_id}
    request_hash = request_digest(class_id, revision_id, content_hash, title)
    async with user_session(factory, ctx) as db:
        await lock_owner(db, ctx)  # before the period lock, as job creation (ADR 0011)
        earlier = (
            await db.execute(BY_KEY, {"owner_id": ctx.user_id, "key": idempotency_key})
        ).first()
        if earlier is not None:
            if earlier.request_hash != request_hash:
                return Outcome("idempotency_mismatch")
            return Outcome(None, earlier.class_id, earlier.id, replay=True)
        not_ready = await revision_ready(db, ctx.user_id, class_id, revision_id)
        if not_ready is not None:
            return Outcome(not_ready)  # a sketch is never copied (ADR 0038 section 4)
        row = (await db.execute(SOURCE, ids)).first()
        if row is None:
            return Outcome("not_found")
        stored = bytes(row.content_hash)
        if stored != content_hash:
            return Outcome("hash_mismatch")
        if not isinstance(row.brief, dict):
            return Outcome("not_ready")
        if is_studio(row.music_source) or is_studio(row.music):
            return Outcome(STUDIO)  # SR C5b: its hash covers the mix spec; not copied (ADR 0036)
        brief: dict[str, Any] = row.brief  # pyright: ignore[reportUnknownVariableType]
        sport = sport_of_brief(brief.get("sport"))
        # The one hash (``design.revision_hash``): an edited draft's covers its overlay (#1424).
        content: Any = row.content
        if sport is None or not isinstance(content, dict):
            return Outcome("internal")
        if revision_hash(content, overlay=row.edit_overlay) != stored:  # pyright: ignore[reportUnknownArgumentType]
            return Outcome("internal")
        if has_retired_game(row.content):
            return Outcome(RETIRED)  # ADR 0029 section 3: before the strict ``spec_of`` (a 500)
        if spec_of(row.content, sport) is None or not isinstance(row.content, dict):
            return Outcome("internal")
        layer = await chat.stated_layer(db, ctx.user_id, class_id, row.id)  # #907
        try:
            made = await described(db, ctx.user_id, class_id, row.id, row.provenance)
            provenance = provenance_copy(
                made,
                class_id,
                row.id,
                row.number,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                stated=layer,
            )
        except ValidationError:
            return Outcome("internal")
        refusal = await flag_refusal(db, settings, ctx, sport, row.music_source, row.music)
        if refusal is not None:
            return Outcome(refusal)
        plan, gates = await current_gates(db, ctx)
        check_brief(gates, brief, plan.id)  # QuotaRefused: length or feature
        check_content(gates, row.content, plan.id)  # pyright: ignore[reportUnknownArgumentType]
        await ledger.take_class(db, ctx)  # QuotaRefused: classes
        new_class, new_revision = uuid.uuid4(), uuid.uuid4()
        source = music_source_copy(row.music_source)
        await db.execute(
            NEW_CLASS,
            {
                "id": new_class,
                "owner_id": ctx.user_id,
                "title": title,
                "brief": _json(brief),
                "music_source": None if source is None else _json(source),
            },
        )
        await db.execute(
            NEW_REVISION,
            {
                "id": new_revision,
                "owner_id": ctx.user_id,
                "class_id": new_class,
                "content": _json(row.content),
                "content_hash": revision_hash(row.content),  # the copy has no overlay
                "music": _json(music_copy(row.music)),
                "provenance": _json(provenance),
            },
        )
        adopted = await db.execute(
            ADOPT, {"owner_id": ctx.user_id, "class_id": new_class, "revision_id": new_revision}
        )
        if adopted.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
            raise RuntimeError("the duplicate's revision was not made current")
        fingerprint = corrections_fingerprint(source, row.music)
        if fingerprint is not None:
            await db.execute(
                COPY_CORRECTIONS,
                {
                    "owner_id": ctx.user_id,
                    "class_id": class_id,
                    "new_class_id": new_class,
                    "fingerprint": fingerprint,
                    "cap": MAX_PER_CLASS,
                },
            )
    return Outcome(None, new_class, new_revision)


def _refuse(reason: str, status: int) -> JSONResponse:
    return JSONResponse({"detail": {"reason": reason}}, status_code=status)


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


STATUS: Final = {"not_found": 404, "internal": 500}


@router.post(
    "/{class_id}/duplicate",
    response_model=DuplicateOut,
    status_code=201,
    responses={404: {}, 409: {}, 422: {}, 500: {}},
)
async def duplicate_class(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> DuplicateOut | JSONResponse:
    """201 with the new class, 200 with the same one for a replayed key. 400 for the header;
    409 reasons: ``idempotency_mismatch``, ``hash_mismatch``, ``not_ready``, ``game_retired``,
    ``studio_not_duplicable``,
    ``feature_off`` and ``quota`` (the jobs API's body: ``quota`` ``classes``, ``length`` or
    ``feature``, no money)."""
    if idempotency_key is None:
        return _refuse("idempotency_key_missing", 400)
    if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        return _refuse("idempotency_key_invalid", 400)
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found", 404)
    try:
        body = DuplicateIn.model_validate_json(await request.body())
    except ValidationError:
        return _refuse("invalid_input", 422)
    title = clean_title(body.title)
    if title is None or not HASH.fullmatch(body.content_hash):
        return _refuse("invalid_input", 422)
    settings: Settings = request.app.state.settings
    ledger = quota.MeteredLedger(quota.Bounds.from_settings(settings))
    try:
        done = await duplicate(
            request.app.state.session_factory,
            settings,
            ledger,
            ctx,
            wanted,
            body.revision_id,
            bytes.fromhex(body.content_hash),
            title,
            idempotency_key,
        )
    except QuotaRefused as refused:  # nothing was created
        return JSONResponse({"detail": {**refused.detail(), "reason": "quota"}}, status_code=409)
    if done.reason is not None or done.class_id is None or done.revision_id is None:
        reason = done.reason or "internal"
        return _refuse(reason, STATUS.get(reason, 409))
    made = DuplicateOut(class_id=done.class_id, revision_id=done.revision_id)
    if done.replay:
        return JSONResponse(made.model_dump(mode="json"), status_code=200)
    return made
