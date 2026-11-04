"""Soundless classes at the edges of the jobs (ADR 0007 amendment of 2026-11-03, items 1 to 3;
#527), modelled on ``classbeat.design.sport``.

A class's music **kind** is ``none`` (``{"source": "none"}``, a soundless class) or not. This
module holds the checks that are not the input's own validation:

- **The gate** (item 2). ``CLASSBEAT_SOUNDLESS_ENABLED`` is the environment's kill switch
  (``SoundlessGate.enabled``, set by the web app and the worker at start). Soundless is on only
  when all three hold: that switch, a stored ``soundless_enabled`` of exactly ``on`` (missing,
  ``null`` or invalid is off; ``flags.core.soundless_switch``), and a ``design`` route covering
  soundless for every plan's ``route_key`` (``flags.effective.soundless_enabled``, read on every
  request). ``admit_soundless`` runs in ``design_draft``'s ``admit``, before the job row and
  before any credit is reserved (``jobs.user.create`` calls ``admit`` first), in this order:

  1. the switch (environment and stored value): off is ``feature_off``;
  2. the job's **own** plan's ``route_key`` covers soundless: otherwise
     ``soundless_not_evaluated`` (checked before step 3, so this answer is reachable: step 3
     includes the user's plan and would answer ``feature_off`` first);
  3. every plan's ``route_key`` covers soundless (the effective flag the form reads): otherwise
     ``feature_off``, so a plan added or a route cleared later refuses even a covered user.

  A queued job ends ``feature_off`` in the worker when the switch is off by then (``still_on``)
  and ``model_config`` when its attempt's model does not cover soundless
  (``require_covered``), both before any call: the backstop for a race with the admin.
- **The web's ``fake``.** The web process has no provider; there the ``fake`` model provider
  counts as covering soundless only when its **own** ``CLASSBEAT_MODEL_PROVIDER`` is ``fake`` and
  ``CLASSBEAT_ENVIRONMENT`` is not ``prod`` (``SoundlessGate.from_settings``), so a production
  web never treats anything as covered without a recorded route. ``compose.yaml`` passes no
  ``CLASSBEAT_MODEL_PROVIDER`` to web, so a plain dev stack cannot switch soundless on; only a
  stack that sets it for web (the e2e stack, SL7) can.
- **The kind never changes** (item 1). ``admit_music`` (in ``admit`` when the job names a class):
  ``none`` on a class whose music is not ``none``, or other music on a class whose music is
  ``none``, is ``invalid_input``; ``check_stored`` repeats it in the final fenced store after
  ``revision.adopt_first``, so two queued jobs of different kinds cannot both land.
- **No songs and no Swedebeat** (item 9, #530). ``refuse_soundless`` is the explicit
  ``invalid_input`` of ``design_with_songs`` (every mode), ``revise_songs``, ``swedebeat_match``,
  the hand-off and ``swedebeat_verify_mix`` for a soundless class; their final fenced stores call
  ``check_stored(fs, soundless=False)`` (the hand-off's adopt refuses a ``none`` source in its own
  compare-and-set), so none of them writes over a ``none`` source.
- **No music step** (item 3). ``NoMusicStep`` stands in for ``design.music.MusicStep``: no MOWL or
  Spotify call, and its ``still_current`` and ``close`` do nothing (the revision names no MOWL
  object).

Without a configured gate the switch is off, so soundless is refused everywhere.
"""

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.flags import effective
from classbeat.jobs import FencedSession, JobFailure, JobRun
from classbeat.jobs.kinds import Refused
from classbeat.models.jobs import GroupModel

NONE: Final = "none"
NO_MUSIC: Final[dict[str, str]] = {"source": NONE}  # classes.music_source and revision music


@dataclass(frozen=True, slots=True)
class SoundlessGate:
    enabled: bool = False  # CLASSBEAT_SOUNDLESS_ENABLED
    # The web's own ``fake`` provider outside prod (see the module); never true in prod.
    fake_covers: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> "SoundlessGate":
        return cls(
            enabled=settings.soundless_enabled,
            fake_covers=fake_covers(settings),
        )


def fake_covers(settings: Settings) -> bool:
    """Whether this process's ``fake`` provider counts as covering soundless: only when its own
    ``CLASSBEAT_MODEL_PROVIDER`` is ``fake`` and ``CLASSBEAT_ENVIRONMENT`` is not ``prod``."""
    return settings.model_provider == "fake" and settings.environment != "prod"


_gate = SoundlessGate()


def configure(gate: SoundlessGate | None) -> None:
    """Set the process's gate (None: soundless off)."""
    global _gate
    _gate = gate or SoundlessGate()


def current() -> SoundlessGate:
    return _gate


def is_none(music: Any) -> bool:
    """Whether a stored music value (``classes.music_source`` or ``class_revisions.music``, a
    dict) is ``none``. Anything else, ``NULL`` included, is not."""
    return isinstance(music, dict) and music.get("source") == NONE  # pyright: ignore[reportUnknownMemberType]


async def soundless_on(db: AsyncSession) -> bool:
    """The effective ``soundless_enabled`` (switch and every plan covered), as the form sees it."""
    gate = current()
    return await effective.soundless_enabled(db, gate.enabled, fake_covers=gate.fake_covers)


async def _plan_covers(db: AsyncSession, ctx: UserContext) -> bool:
    if current().fake_covers:
        return True
    from classbeat.models.routes import covers  # the web loads the stage definitions lazily
    from classbeat.plans.resolve import effective_plan

    plan = await effective_plan(db, ctx)
    return await covers(db, plan.route_key, "soundless")


async def admit_soundless(db: AsyncSession, ctx: UserContext) -> Refused | None:
    """A soundless design at creation, in the order of the module: the switch (``feature_off``),
    the job's own plan's route (``soundless_not_evaluated``), every plan's route (``feature_off``).
    None admits. Reads only; reserves nothing."""
    gate = current()
    if not await effective.soundless_switch(db, gate.enabled):
        return Refused("feature_off")
    if not await _plan_covers(db, ctx):
        return Refused("soundless_not_evaluated")
    if not await soundless_on(db):
        return Refused("feature_off")
    return None


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
    if known is not None and is_none(known) != soundless:
        return Refused("invalid_input")
    return None


async def refuse_soundless(db: AsyncSession, owner_id: UUID, class_id: UUID) -> Refused | None:
    """Song picking, the Swedebeat match, the hand-off and ``swedebeat_verify_mix`` of a class
    (item 9, #530): ``invalid_input`` when the class is soundless (its ``music_source``, or its
    newest revision's music while it has none, is ``none``), read explicitly rather than inferred
    from a missing song list or match. None admits. Reads only."""
    return await admit_music(db, owner_id, False, class_id)


_STORED_SOURCE = text(
    "SELECT music_source FROM classes WHERE owner_id = :owner_id AND id = :class_id"
)


async def admit_class(db: AsyncSession, owner_id: UUID, class_id: UUID) -> Refused | None:
    """A revise of a class: a soundless class cannot be revised until SL2 (#528) adds its stage,
    so it is ``feature_off``, before anything is reserved."""
    source = (
        await db.execute(_STORED_SOURCE, {"owner_id": owner_id, "class_id": class_id})
    ).scalar_one_or_none()
    return Refused("feature_off") if is_none(source) else None


async def still_on(run: JobRun) -> None:
    """In the worker, before a soundless job calls anything: ``feature_off`` when the switch
    (environment and stored value) is off now. Coverage is the model's (``require_covered``)."""
    async with run.fenced() as fs:
        on = await effective.soundless_switch(fs.db, current().enabled)
    if not on:
        raise JobFailure("feature_off")


def require_covered(model: GroupModel) -> None:
    """In the worker, after the ``design`` group's model is resolved and before anything is
    called: ``model_config`` unless that model covers soundless."""
    if not model.soundless_covered:
        raise JobFailure("model_config")


_JOB_CLASS_SOURCE = text(
    "SELECT c.music_source FROM classes c JOIN jobs j ON j.owner_id = c.owner_id "
    "AND j.class_id = c.id WHERE j.id = :job_id AND j.owner_id = :owner_id"
)


async def check_stored(fs: FencedSession, soundless: bool) -> None:
    """In a design's final fenced transaction, after ``revision.adopt_first``: a soundless
    revision needs the class's ``music_source`` to be ``none``, and a music revision a class whose
    source is not ``none``. Otherwise (a job of the other kind landed first) ``invalid_input``,
    which rolls the whole store back."""
    source = (
        await fs.db.execute(
            _JOB_CLASS_SOURCE, {"job_id": fs.lease.job_id, "owner_id": fs.lease.owner_id}
        )
    ).scalar_one_or_none()
    if is_none(source) != soundless:
        raise JobFailure("invalid_input")


@dataclass
class NoMusicStep:
    """The music step of a soundless job: nothing was read, nothing is held."""

    async def still_current(self, fs: FencedSession) -> None:
        """Nothing to check: the revision names no MOWL object (Save holds the connection)."""

    def close(self) -> None:
        """Nothing to drop: no connection was opened."""
