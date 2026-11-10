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
   stored brief is 409 ``not_ready``;
2. (``classbeat.classes.duplicate_gates``) a running class is admitted only by
   ``design.sport.admit_running``, as a running design is
   (``feature_off``, or ``running_not_evaluated`` for the user's uncovered plan; ADR 0018 Amendment
   5, #509), and a class or draft on a Swedebeat mix while ``swedebeat_enabled`` is off (the
   class's current source **or** the chosen revision's music on a mix: the stricter rule, on
   purpose) is 409 ``feature_off``; a soundless class (``{source:
   "none"}``, #526) is admitted only by ``design.music_kind.admit_soundless``, as a soundless
   design is (``feature_off``, or ``soundless_not_evaluated`` for the user's uncovered plan), and
   its copy stays ``none`` (source and revision music);
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

import hashlib
import json
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import auth, quota
from classbeat.agent.beat_one import MAX_PER_CLASS
from classbeat.agent.records import Provenance
from classbeat.classes.duplicate_gates import flag_refusal
from classbeat.classes.duplicate_music import (
    HASH,
    corrections_fingerprint,
    music_copy,
    music_source_copy,
)
from classbeat.config import Settings
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design import chat
from classbeat.design.games_policy import RETIRED, has_retired_game
from classbeat.jobs.credits import QuotaRefused
from classbeat.jobs.http import IDEMPOTENCY_KEY, canonical_json
from classbeat.jobs.user import lock_owner
from classbeat.quota.gates import check_brief, check_content, current_gates
from classbeat.saving.approval import spec_of, sport_of_brief

Factory = async_sessionmaker[AsyncSession]

router = APIRouter(prefix="/api/classes", tags=["classes"])

TITLE_MAX: Final = 100  # the classes_title check of migration 0004
# The descriptive fields of the source revision's provenance: how the content was made. Run ids,
# usage, cost and the lineage of the source class are never copied.
DESCRIPTIVE: Final = (
    "provider",
    "model",
    "model_answered",
    "effort",
    "prompt_version",
    "classbeat_version",
    "mowl_version",
    "map_version",
    "crossfade_sec",
    "route_key",
    "eval_result_id",
    "tss_approximate",
    "rules",
    "sport",
)
ZERO_USAGE: Final = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
}

_SOURCE = text(
    "SELECT r.id, r.number, r.content, r.content_hash, r.music, r.provenance, c.brief, "
    "c.music_source FROM classes c JOIN class_revisions r ON r.owner_id = c.owner_id "
    "AND r.class_id = c.id AND r.id = :revision_id AND r.kind = 'draft' "
    "WHERE c.owner_id = :owner_id AND c.id = :class_id"
)
# The user's own duplicate made with this key, if any (the replay of D item 7).
_BY_KEY = text(
    "SELECT class_id, id, provenance -> 'duplicated_from' ->> 'request_hash' AS request_hash "
    "FROM class_revisions WHERE owner_id = :owner_id AND kind = 'draft' "
    "AND provenance ->> 'origin' = 'duplicate' "
    "AND provenance -> 'duplicated_from' ->> 'idempotency_key' = :key LIMIT 1"
)
_NEW_CLASS = text(
    "INSERT INTO classes (id, owner_id, title, brief, music_source) "
    "VALUES (:id, :owner_id, :title, CAST(:brief AS jsonb), CAST(:music_source AS jsonb))"
)
_NEW_REVISION = text(
    "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, content_hash, "
    "music, provenance, job_id, parent_id) VALUES (:id, :owner_id, :class_id, 1, 'draft', "
    "CAST(:content AS jsonb), :content_hash, CAST(:music AS jsonb), CAST(:provenance AS jsonb), "
    "NULL, NULL)"
)
# DU3: the source class's corrections for the chosen revision's fingerprint, re-keyed to the copy.
# Both sides name the caller's ``owner_id`` (and row security holds them to it too); ``id``,
# ``created_at`` and ``updated_at`` are the table's defaults.
_COPY_CORRECTIONS = text(
    "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
    "SELECT :owner_id, :new_class_id, fingerprint, song, beats, bpm FROM beat_one_corrections "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND fingerprint = :fingerprint "
    "ORDER BY song LIMIT :cap"
)
_ADOPT = text(
    "UPDATE classes SET current_revision_id = :revision_id "
    "WHERE owner_id = :owner_id AND id = :class_id AND current_revision_id IS NULL"
)


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


def clean_title(raw: str) -> str | None:
    """The title with line breaks, tabs and other blanks turned into spaces, control and format
    characters removed and blanks collapsed, 1 to 100 characters; ``None`` otherwise."""
    kept = "".join(
        " " if ch.isspace() else ch
        for ch in raw
        if ch.isspace() or unicodedata.category(ch)[0] != "C"
    )
    title = " ".join(kept.split())
    return title if 1 <= len(title) <= TITLE_MAX else None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownArgumentType]


def request_digest(class_id: UUID, revision_id: UUID, content_hash: bytes, title: str) -> str:
    """The SHA-256 (hex) of the canonical request a key stands for."""
    request = {
        "class_id": str(class_id),
        "revision_id": str(revision_id),
        "content_hash": content_hash.hex(),
        "title": title,
    }
    return hashlib.sha256(canonical_json(request)).hexdigest()


def provenance_copy(
    source: Any,
    class_id: UUID,
    revision_id: UUID,
    number: int,
    *,
    idempotency_key: str,
    request_hash: str,
    stated: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The duplicate's provenance (``ValidationError`` when the source's no longer checks);
    ``stated`` is the source revision's nearest stated layer (#907), or ``None`` for none."""
    found = _dict(source)
    fields = {k: found[k] for k in DESCRIPTIVE if k in found}
    built = Provenance.model_validate(
        {
            **fields,
            "origin": "duplicate",
            "duplicated_from": {
                "class_id": str(class_id),
                "revision_id": str(revision_id),
                "number": number,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
            },
            **({} if stated is None else {"stated": stated}),
            "agent_run_ids": [],
            "usage": ZERO_USAGE,
            "cost_micros": 0,
        },
        strict=False,
    )
    out = built.model_dump(mode="json")
    if stated is not None:
        out["stated"] = (
            stated  # as the source stored it (checked above), not with defaults filled in
        )
    return out


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
            await db.execute(_BY_KEY, {"owner_id": ctx.user_id, "key": idempotency_key})
        ).first()
        if earlier is not None:
            if earlier.request_hash != request_hash:
                return Outcome("idempotency_mismatch")
            return Outcome(None, earlier.class_id, earlier.id, replay=True)
        row = (await db.execute(_SOURCE, ids)).first()
        if row is None:
            return Outcome("not_found")
        stored = bytes(row.content_hash)
        if stored != content_hash:
            return Outcome("hash_mismatch")
        if not isinstance(row.brief, dict):
            return Outcome("not_ready")
        brief: dict[str, Any] = row.brief  # pyright: ignore[reportUnknownVariableType]
        sport = sport_of_brief(brief.get("sport"))
        canonical = json.dumps(
            row.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        if sport is None or hashlib.sha256(canonical).digest() != stored:
            return Outcome("internal")
        if has_retired_game(row.content):
            return Outcome(RETIRED)  # ADR 0029 section 3: before the strict ``spec_of`` (a 500)
        if spec_of(row.content, sport) is None or not isinstance(row.content, dict):
            return Outcome("internal")
        layer = await chat.stated_layer(db, ctx.user_id, class_id, row.id)  # #907
        try:
            provenance = provenance_copy(
                row.provenance,
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
            _NEW_CLASS,
            {
                "id": new_class,
                "owner_id": ctx.user_id,
                "title": title,
                "brief": _json(brief),
                "music_source": None if source is None else _json(source),
            },
        )
        await db.execute(
            _NEW_REVISION,
            {
                "id": new_revision,
                "owner_id": ctx.user_id,
                "class_id": new_class,
                "content": _json(row.content),
                "content_hash": stored,
                "music": _json(music_copy(row.music)),
                "provenance": _json(provenance),
            },
        )
        adopted = await db.execute(
            _ADOPT, {"owner_id": ctx.user_id, "class_id": new_class, "revision_id": new_revision}
        )
        if adopted.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
            raise RuntimeError("the duplicate's revision was not made current")
        fingerprint = corrections_fingerprint(source, row.music)
        if fingerprint is not None:
            await db.execute(
                _COPY_CORRECTIONS,
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
