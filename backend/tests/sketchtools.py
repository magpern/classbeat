"""Helpers for the composer's sketch tests (ADR 0038 sections 4 and 8, #1186): a class read with
``composer_flow`` on, handed off to its sketch, the sketch designed by the worker side with the
soundless ``FakeProvider`` script. The soundless gate is on (the sketch's stage is the soundless
one). Never the network, a real account or a model."""

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest

from classbeat.design import composer, music_kind
from interprettools import InterpretEnv
from revisetools import ReviseEnv

NO_MUSIC: dict[str, str] = {"source": "none"}


def composer_flag(renv: ReviseEnv, value: Any) -> None:
    """The stored ``composer_flow`` (JSON), or no row for ``None``."""
    renv.sql("DELETE FROM app_settings WHERE key = 'composer_flow'")
    if value is not None:
        renv.sql(
            "INSERT INTO app_settings (key, value) VALUES ('composer_flow', CAST(:v AS jsonb))",
            v=json.dumps(value),
        )


def composer_on(renv: ReviseEnv, on: bool = True) -> None:
    composer.configure(composer.ComposerGate(enabled=on))
    composer_flag(renv, "on" if on else None)


class SketchEnv:
    def __init__(self, ienv: InterpretEnv) -> None:
        self.ienv = ienv

    @property
    def renv(self) -> ReviseEnv:
        return self.ienv.renv

    @property
    def sql(self) -> Any:
        return self.ienv.sql

    async def interpreted(self, *, music: bool = True) -> UUID:
        """A class read with the composer flow on: with the user's playlist, or "No music"."""
        more = {} if music else {"music_override": NO_MUSIC}
        class_id, _ = await self.ienv.interpreted(**more)
        return class_id

    async def designed(self, class_id: UUID) -> UUID:
        """Hand the class off and run its design with the soundless script: its revision id."""
        made = await self.ienv.handoff(class_id)
        assert made.status_code == 201, made.text
        self.renv.use("soundless_first_draft")
        await self.renv.run("design_draft")
        row = self.renv.denv.ops.row(UUID(made.json()["job_id"]))
        assert (row.state, row.error_category) == ("completed", None)
        current = self.renv.current(class_id)
        assert current is not None
        return current

    async def sketched(self) -> tuple[UUID, UUID]:
        """A composer class whose current revision is its sketch: (class id, sketch id)."""
        class_id = await self.interpreted()
        return class_id, await self.designed(class_id)

    def revision(self, revision_id: UUID) -> Any:
        return self.sql("SELECT * FROM class_revisions WHERE id = :r", r=revision_id).one()

    def music_source(self, class_id: UUID) -> Any:
        return self.sql("SELECT music_source FROM classes WHERE id = :c", c=class_id).scalar()


@pytest.fixture
async def skenv(ienv: InterpretEnv) -> AsyncIterator[SketchEnv]:
    composer_on(ienv.renv)
    music_kind.configure(music_kind.SoundlessGate(fake_covers=True))
    try:
        yield SketchEnv(ienv)
    finally:
        composer.configure(None)
        composer_flag(ienv.renv, None)
        music_kind.configure(None)
