"""Soundless classes at the edges of the jobs (ADR 0007 amendments of 2026-11-03, items 1 to 3,
and 2026-11-27; #527, #1543), modelled on ``classbeat.design.sport``.

A class's music **kind** is ``none`` (``{"source": "none"}``, a soundless class) or not. This
module holds the checks that are not the input's own validation:

- **No switch** (ADR 0007 amendment of 2026-11-27, #1543; owner, 2026-11-26: "No music" is always
  a choice). Nothing hides or refuses a soundless design, revise, duplicate, Save or Update on a
  switch or a flag: the environment's soundless switch and the effective flag of the 2026-11-03
  and 2026-11-08 amendments are gone. The checks below (the kind, the studio, the user's own
  rows) and Save's approval are unchanged.
- **Model coverage** (ADR 0012 section 5). A soundless job uses the soundless prompts on the
  ``design`` group's model, resolved as for any job (the route, or the bootstrap choice without
  one; a route with a newer failure is still ``model_config``). ``serve_model`` runs in the worker
  after the model is resolved and before anything is called: a model with no recorded soundless
  coverage (``GroupModel.soundless_covered``) serves a real no-music class with the stale-route
  warning, once per process and route key, instead of refusing; a composer's **sketch** keeps its
  own rule (ADR 0038 amendment of 2026-11-24): it is admitted by ``sketch_gate.admit_sketch`` and
  ends ``model_config`` on an uncovered model (``require_covered``).
- **The web's ``fake``.** The web process has no provider; there the ``fake`` model provider
  counts as covering soundless only when its **own** ``CLASSBEAT_MODEL_PROVIDER`` is ``fake`` and
  ``CLASSBEAT_ENVIRONMENT`` is not ``prod`` (``SoundlessGate.from_settings``), so a production
  web never treats anything as covered without a recorded route (``plan_covers``, the sketch's).
- **The kind never changes** (item 1). ``admit_music`` (in ``admit`` when the job names a class):
  ``none`` on a class whose music is not ``none``, or other music on a class whose music is
  ``none``, is ``invalid_input``; ``check_stored`` repeats it in the final fenced store before
  ``revision.adopt_first`` (#589; the soundless store with ``before_adopt``, then strictly after
  it), so two queued jobs of different kinds cannot both land. A **studio class** (ADR 0036
  section 2: a stored source or newest revision of ``swedebeat_studio``) refuses every job of
  another kind here and in ``refuse_soundless``; the studio's own ``studio_songs`` is admitted
  only by ``admit_studio`` (no music yet, or the studio's), checked again by ``still_studio``
  and, in the final stores, by ``check_stored`` (``studio``; any studio revision counts there).
- **A revise of a soundless class** (SL2, #528) is admitted by ``admit_class`` (a studio class
  is refused; nothing else gates it); in the worker ``serve_model`` runs before any call, as for
  a design. Its final store repeats ``check_stored``.
- **No songs and no Swedebeat** (item 9, #530). ``refuse_soundless`` is the explicit
  ``invalid_input`` of ``design_with_songs`` (every mode), ``revise_songs``, ``swedebeat_match``,
  the hand-off and ``swedebeat_verify_mix`` for a soundless class; their final fenced stores call
  ``check_stored(fs, soundless=False)`` (the hand-off's adopt refuses a ``none`` source in its own
  compare-and-set), so none of them writes over a ``none`` source. ``swedebeat_match`` and
  ``swedebeat_verify_mix`` also call ``still_has_music`` in the worker before their outside calls
  (#470), so a class soundless by then gets no catalogue lookup, mix listing or attach.
- **No music step** (item 3). ``NoMusicStep`` stands in for ``design.music.MusicStep``: no MOWL or
  Spotify call, and its ``still_current`` and ``close`` do nothing (the revision names no MOWL
  object).
"""

import logging
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.design.music_kind_gate import SoundlessGate as SoundlessGate  # re-exported
from classbeat.design.music_kind_gate import configure as configure
from classbeat.design.music_kind_gate import current
from classbeat.design.music_kind_gate import fake_covers as fake_covers
from classbeat.jobs import FencedSession, JobFailure
from classbeat.jobs.kinds import Refused
from classbeat.models.jobs import GroupModel

log = logging.getLogger(__name__)

NONE: Final = "none"
STUDIO: Final = "swedebeat_studio"  # ADR 0036 section 2: only the studio's own kinds act on it
NO_MUSIC: Final[dict[str, str]] = {"source": NONE}  # classes.music_source and revision music
# The stale-route warning's reason for a model with no recorded soundless coverage (ADR 0012
# section 5, #1543): logged, never refused, for a real no-music class.
UNCOVERED: Final = "soundless_not_evaluated"


def is_none(music: Any) -> bool:
    """Whether a stored music value (``classes.music_source`` or ``class_revisions.music``, a
    dict) is ``none``. Anything else, ``NULL`` included, is not."""
    return isinstance(music, dict) and music.get("source") == NONE  # pyright: ignore[reportUnknownMemberType]


def is_studio(music: Any) -> bool:
    """Whether a stored music value is the SwedeBeat studio's (ADR 0036 section 2)."""
    return isinstance(music, dict) and music.get("source") == STUDIO  # pyright: ignore[reportUnknownMemberType]


async def plan_covers(db: AsyncSession, ctx: UserContext) -> bool:
    """Whether the job's own plan's ``design`` route covers soundless (the sketch gate's check;
    the web's ``fake`` outside prod counts)."""
    if current().fake_covers:
        return True
    from classbeat.models.routes import covers  # the web loads the stage definitions lazily
    from classbeat.plans.resolve import effective_plan

    plan = await effective_plan(db, ctx)
    return await covers(db, plan.route_key, "soundless")


# The class's music kind so far: its stored ``music_source``, else its newest revision's music
# (a class whose songs were picked before its design), else none yet. The user's own rows only.
_CLASS_MUSIC = text(
    "SELECT c.music_source, (SELECT r.music FROM class_revisions r WHERE r.owner_id = c.owner_id "
    "AND r.class_id = c.id ORDER BY r.number DESC LIMIT 1) AS newest "
    "FROM classes c WHERE c.owner_id = :owner_id AND c.id = :class_id"
)


async def admit_music(
    db: AsyncSession, owner_id: UUID, soundless: bool, class_id: UUID | None
) -> Refused | None:
    """A design job's music kind against its class's: ``invalid_input`` when the class has music
    (a stored source or a revision) of the other kind. None admits (a new class, or a class with
    neither yet)."""
    if class_id is None:
        return None
    row = (await db.execute(_CLASS_MUSIC, {"owner_id": owner_id, "class_id": class_id})).first()
    if row is None:
        return None  # the generic checks answered ``class_not_found`` already
    known = row.music_source if row.music_source is not None else row.newest
    if is_studio(known):  # a studio class: no other kind's job, ever (ADR 0036 section 2)
        return Refused("invalid_input")
    if known is not None and is_none(known) != soundless:
        return Refused("invalid_input")
    return None


async def admit_studio(db: AsyncSession, owner_id: UUID, class_id: UUID) -> Refused | None:
    """A studio job (``studio_songs``) of a class: ``invalid_input`` when the class has music of
    another kind (a stored source or a newest revision that is not the studio's, soundless
    included). None admits (a studio class, or one with no music yet). Reads only."""
    row = (await db.execute(_CLASS_MUSIC, {"owner_id": owner_id, "class_id": class_id})).first()
    if row is None:
        return None  # the generic checks answered ``class_not_found`` already
    known = row.music_source if row.music_source is not None else row.newest
    return None if known is None or is_studio(known) else Refused("invalid_input")


async def still_studio(fs: FencedSession, class_id: UUID) -> None:
    """``admit_studio`` again in the worker, before a studio job calls anything outside."""
    if await admit_studio(fs.db, fs.lease.owner_id, class_id) is not None:
        raise JobFailure("invalid_input")


async def refuse_soundless(db: AsyncSession, owner_id: UUID, class_id: UUID) -> Refused | None:
    """Song picking, the Swedebeat match, the hand-off and ``swedebeat_verify_mix`` of a class
    (item 9, #530): ``invalid_input`` when the class is soundless (its ``music_source``, or its
    newest revision's music while it has none, is ``none``) or a studio class (ADR 0036), read
    explicitly rather than inferred from a missing song list or match. None admits. Reads only."""
    return await admit_music(db, owner_id, False, class_id)


async def still_has_music(fs: FencedSession, class_id: UUID) -> None:
    """In the worker, before a job that needs a music class calls anything outside (the match's
    catalogue lookups, the verify job's mix listing and attach; #470, #587): ``invalid_input``
    when the class is soundless by now, read as ``refuse_soundless`` reads it. Reads only; the
    final stores' ``check_stored`` stays the guard of what is written."""
    if await refuse_soundless(fs.db, fs.lease.owner_id, class_id) is not None:
        raise JobFailure("invalid_input")


_STORED_SOURCE = text(
    "SELECT music_source FROM classes WHERE owner_id = :owner_id AND id = :class_id"
)


async def admit_class(db: AsyncSession, ctx: UserContext, class_id: UUID) -> Refused | None:
    """A revise of a class (SL2, #528): a studio class is ``invalid_input`` (ADR 0036 section 2;
    ``studio_revise`` is its own); a music class and a soundless one pass (no switch, #1543), and
    the worker's ``serve_model`` decides the model. None admits. Reads the user's own row only."""
    source = (
        await db.execute(_STORED_SOURCE, {"owner_id": ctx.user_id, "class_id": class_id})
    ).scalar_one_or_none()
    if is_studio(source):  # SR C5b: only the studio's own kinds revise a studio class
        return Refused("invalid_input")
    return None


def require_covered(model: GroupModel) -> None:
    """In the worker, after the ``design`` group's model is resolved and before anything is
    called: ``model_config`` unless that model covers soundless. A sketch's rule only
    (``serve_model``)."""
    if not model.soundless_covered:
        raise JobFailure("model_config")


_warned: set[str | None] = set()


def serve_model(model: GroupModel, *, sketch: bool) -> None:
    """In the worker, after the ``design`` group's model is resolved and before anything is
    called (#1543). A sketch: ``require_covered``. A real no-music class is always served: on a
    model without recorded soundless coverage with the stale-route warning (ADR 0012 section 5),
    once per process and route key (``None``: the bootstrap choice). Logs codes only."""
    if sketch:
        require_covered(model)
        return
    if model.soundless_covered:
        return
    key = model.agent.route_key
    if key in _warned:
        return
    _warned.add(key)
    log.warning(
        "model route stale",
        extra={"route_key": key, "stage_group": "design", "reason": UNCOVERED},
    )


_JOB_CLASS_SOURCE = text(
    "SELECT c.music_source FROM classes c JOIN jobs j ON j.owner_id = c.owner_id "
    "AND j.class_id = c.id WHERE j.id = :job_id AND j.owner_id = :owner_id"
)


# Whether the job's class has a studio revision, and whether it has a revision with other music
# (ADR 0036 section 2): a class's kind never changes, so one of either decides it.
_JOB_CLASS_KINDS = text(
    "SELECT bool_or(r.music ->> 'source' = :studio) AS studio, "
    "bool_or(r.music IS NOT NULL AND r.music ->> 'source' IS DISTINCT FROM :studio) AS other "
    "FROM class_revisions r JOIN jobs j ON j.owner_id = r.owner_id AND j.class_id = r.class_id "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id"
)


async def _check_kind(fs: FencedSession, source: Any, studio: bool) -> None:
    row = (
        await fs.db.execute(
            _JOB_CLASS_KINDS,
            {"job_id": fs.lease.job_id, "owner_id": fs.lease.owner_id, "studio": STUDIO},
        )
    ).one()
    if studio:
        if (source is not None and not is_studio(source)) or row.other:
            raise JobFailure("invalid_input")
    elif is_studio(source) or row.studio:
        raise JobFailure("invalid_input")


async def check_stored(
    fs: FencedSession, soundless: bool, *, before_adopt: bool = False, studio: bool = False
) -> None:
    """In a design's final fenced transaction: a soundless revision needs the class's
    ``music_source`` to be ``none``, and a music revision a class whose source is not ``none``.
    Otherwise (a job of the other kind landed first) ``invalid_input``, which rolls the whole
    store back.

    Call it BEFORE ``revision.adopt_first`` (#589), which writes ``music_source`` whenever the
    class has no current revision yet. A first soundless design has no source until
    ``adopt_first`` writes ``none``, so with ``before_adopt`` a soundless check also accepts an
    unset source (a music source is still refused); the strict check follows ``adopt_first``.

    A studio class (a stored studio source, or any studio revision) refuses every other kind's
    store; the studio's own (``studio``) refuses a class with another source or a revision of other
    music, so a job of the other kind admitted at the same time cannot land second (ADR 0036)."""
    source = (
        await fs.db.execute(
            _JOB_CLASS_SOURCE, {"job_id": fs.lease.job_id, "owner_id": fs.lease.owner_id}
        )
    ).scalar_one_or_none()
    await _check_kind(fs, source, studio)
    if studio or (soundless and before_adopt and source is None):
        return
    if is_none(source) != soundless:
        raise JobFailure("invalid_input")


@dataclass
class NoMusicStep:
    """The music step of a soundless job: nothing was read, nothing is held."""

    async def still_current(self, fs: FencedSession) -> None:
        """Nothing to check: the revision names no MOWL object (Save holds the connection)."""

    def close(self) -> None:
        """Nothing to drop: no connection was opened."""
