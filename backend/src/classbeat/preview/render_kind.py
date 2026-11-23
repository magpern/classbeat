"""The ``preview_render`` job (ADR 0042 section 6.5, ADR 0005): one audition window of a class's
current studio draft, rendered by the existing sandboxed child and kept as a clip. Enqueued only by
the clip endpoint (``classes.preview_clips``), never by the jobs API or a model: its input is no
``JobInput``, so ``POST /api/jobs`` answers ``unknown_kind``.

**Outside the class's job slot.** The job's ``class_id`` is NULL and its class is
``preview_class_id`` (migration 0072: an owner-inclusive foreign key, ``ON DELETE CASCADE``), so it
is outside ``jobs_one_open_per_class``: an edit, an undo or another job of the class goes on while a
clip renders. It is bounded instead by the per-owner rate limits of the clip endpoint
(``session_policy``), the lease and fencing of ADR 0005, and a **spotdown slot** (#574: a render is
as heavy as an analysis), which also makes it yield to the queue's other work.

1. **check** (one fenced read): the studio on and the preview volume configured (``feature_off``);
   the class's current draft and its plan (``plan_source``: ``invalid_input`` for a class with no
   studio draft, ``music_changed`` for an analysis that is gone); the window placed again from the
   input's ``start_ms`` and the key computed again from the draft **now** (its plan, sources,
   window and this worker's renderer identity). A window or key that is not the input's ends the
   job ``invalid_input`` with nothing stored: the audio changed since the request (a workout-only
   edit keeps the key). A clip already stored under the key ends it at once.
2. **sources**: a spotdown slot, the draft's SwedeBeat connection's bearer
   (``open_match_bearer``), then each distinct track from the per-owner source cache or
   **downloaded fresh** with the gateway's read-only download and its existing caps
   (``sources.gather``). **No external write.**
3. **render**: the child with a ``purpose: "preview"`` plan and the window (``render.render_files``;
   a failure is ``analysis_failed``); the clip copied to the owner's path on the volume, then, in
   the final fenced transaction, the class re-checked (``FOR KEY SHARE``: no clip is stored for a
   class deleted while it rendered; ``invalid_input``), the row inserted (``ON CONFLICT (owner_id,
   key) DO NOTHING``: a twin job's clip wins) and the owner's size cap enforced (``purge.cap``).
   The files of a row not stored, and of rows the cap evicted, are removed once the attempt is over.

It consumes no credit and runs no model. Only categories and counts are logged.
"""

import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from classbeat.design.mixfetch import private_directory
from classbeat.jobs import KINDS, Complete, FencedSession, JobFailure, JobKind, JobRun
from classbeat.preview import identity, plan_source, purge, sources, storage
from classbeat.preview.clip_window_calc import ClipWindow, clip_window, render_window
from classbeat.preview.key_calc import KeyInputs, clip_key, plan_sha256
from classbeat.studio import render, render_policy
from classbeat.swedebeat_match import config as match_config
from classbeat.swedebeat_match.kind import open_match_bearer
from classbeat.swedebeat_studio import config as studio_config

KIND: Final = "preview_render"
STAGES: Final = ("check", "sources", "render")
_AS_JOB: Final = {"music_changed": "music_changed", "internal": "internal"}

_CLASS = text("SELECT 1 FROM classes WHERE owner_id = :owner_id AND id = :class_id FOR KEY SHARE")
_HAS_CLIP = text("SELECT 1 FROM preview_clips WHERE owner_id = :owner_id AND key = :key")
_ADD = text(
    "INSERT INTO preview_clips "
    "(id, owner_id, class_id, key, start_ms, end_ms, sha256, bytes) "
    "VALUES (:id, :owner_id, :class_id, :key, :start_ms, :end_ms, :sha256, :bytes) "
    "ON CONFLICT (owner_id, key) DO NOTHING RETURNING id"
)


class PreviewRenderInput(BaseModel):
    """Stored by the clip endpoint: the class, the window it placed and the key it computed."""

    model_config = ConfigDict(extra="forbid")

    preview_class_id: UUID
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    key: str = Field(pattern=r"^[0-9a-f]{64}$")


class _Kept:
    """What the attempt leaves for its ``release``: the clip file, whether its row went in, and
    the files of the rows the size cap evicted."""

    def __init__(self) -> None:
        self.clip: Path | None = None
        self.stored = False
        self.evicted: list[Path] = []
        self.replaced: list[Path] = []

    def release(self) -> None:
        if self.clip is not None and not self.stored:
            storage.remove(self.clip)
        for path in (*self.evicted, *self.replaced):
            storage.remove(path)


async def _checked(run: JobRun, given: PreviewRenderInput) -> tuple[plan_source.PreviewPlan, bool]:
    owner_id = run.lease.owner_id
    async with run.fenced() as fs:
        found = await plan_source.load(fs.db, owner_id, given.preview_class_id)
        params = {"owner_id": owner_id, "key": bytes.fromhex(given.key)}
        stored = (await fs.db.execute(_HAS_CLIP, params)).first() is not None
    if isinstance(found, str):
        raise JobFailure(_AS_JOB.get(found, "invalid_input"))
    try:
        window = clip_window(found.plan, given.start_ms)
    except ValueError:
        raise JobFailure("invalid_input") from None
    if window != ClipWindow(given.start_ms, given.end_ms):
        raise JobFailure("invalid_input")
    inputs = KeyInputs(plan_sha256(found.plan), found.sources, window, identity.current())
    if clip_key(inputs) != given.key:
        raise JobFailure("invalid_input")  # the audio (or the renderer) changed since the request
    return found, stored


def _store(
    given: PreviewRenderInput, clip: Path, sha256: str, kept: _Kept
) -> Callable[[FencedSession], Awaitable[None]]:
    clip_id, size = UUID(clip.stem), clip.stat().st_size

    async def store(fs: FencedSession) -> None:
        owner_id = fs.lease.owner_id
        ids = {"owner_id": owner_id, "class_id": given.preview_class_id}
        if (await fs.db.execute(_CLASS, ids)).first() is None:
            raise JobFailure("invalid_input")  # deleted while it rendered: nothing is stored
        added = await fs.db.execute(
            _ADD,
            {
                **ids,
                "id": clip_id,
                "key": bytes.fromhex(given.key),
                "start_ms": given.start_ms,
                "end_ms": given.end_ms,
                "sha256": bytes.fromhex(sha256),
                "bytes": size,
            },
        )
        kept.stored = added.first() is not None
        root = storage.current()
        if root is not None:
            kept.evicted = await purge.cap(fs.db, root, owner_id)

    return store


async def run(job_run: JobRun) -> Complete:
    given = job_run.input
    if not isinstance(given, PreviewRenderInput) or job_run.approved:
        raise JobFailure("internal")
    root = storage.current()
    if root is None or not studio_config.current().design:
        raise JobFailure("feature_off")
    await job_run.stage("check")
    found, stored = await _checked(job_run, given)
    if stored:
        return Complete()  # a twin job rendered it meanwhile
    if found.connection_id is None:
        raise JobFailure("not_connected")
    job_run.require_spotdown_slot()  # #574: requeued with one, the attempt not counted
    cfg = match_config.current()
    bearer = await open_match_bearer(job_run, cfg, UUID(found.connection_id))
    if cfg.swedebeat_gateway_key is None:  # pragma: no cover - checked by the open
        raise JobFailure("internal")
    await job_run.stage("sources")
    kept = _Kept()
    try:
        with private_directory() as work:
            got = await sources.gather(
                job_run,
                bearer,
                cfg.swedebeat_gateway_key,
                root,
                given.preview_class_id,
                found.sources,
                work,
            )
            kept.replaced = got.replaced
            tracks = [
                render_policy.track_of(part, str(got.paths[str(part["reference"])]))
                for part in found.plan
            ]
            await job_run.stage("render")
            window = render_window(ClipWindow(given.start_ms, given.end_ms), found.plan)
            try:
                rendered = await render.render_files(work, tracks, window=window)
            except render.RenderFailed:
                raise JobFailure("analysis_failed") from None
            owner_id = job_run.lease.owner_id
            kept.clip = storage.place(rendered.path, root, owner_id, "clips", uuid.uuid4())
            if kept.clip is None:
                raise JobFailure("internal")
            store = _store(given, kept.clip, rendered.sha256, kept)
    except BaseException:
        kept.release()
        raise
    return Complete(store, kept.release)


PREVIEW_RENDER: Final = KINDS.register(
    JobKind(KIND, PreviewRenderInput, STAGES, run, consumes_credit=False)
)
