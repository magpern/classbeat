"""CP-R2 (#1394): the final fenced step of ``preview_render`` re-checks the class (``FOR KEY
SHARE``) before it stores a clip. A class deleted while the clip rendered stores nothing and ends
the job ``invalid_input``; a class still there stores the row. Fakes only: no database."""

import uuid
from pathlib import Path
from typing import Any

import pytest

from classbeat.jobs import JobFailure
from classbeat.preview import render_kind


class Result:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def first(self) -> object | None:
        return self._row


class Db:
    """Answers the class re-check with ``class_row`` and records every statement it is given."""

    def __init__(self, class_row: object | None) -> None:
        self.class_row = class_row
        self.seen: list[Any] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> Result:
        self.seen.append(statement)
        if statement is render_kind._CLASS:  # pyright: ignore[reportPrivateUsage]
            return Result(self.class_row)
        return Result((1,))


class Fenced:
    def __init__(self, db: Db) -> None:
        self.db = db
        self.lease = type("Lease", (), {"owner_id": uuid.uuid4()})()


def given() -> render_kind.PreviewRenderInput:
    return render_kind.PreviewRenderInput(
        preview_class_id=uuid.uuid4(), start_ms=0, end_ms=60_000, key="ab" * 32
    )


def clip_file(tmp_path: Path) -> Path:
    clip = tmp_path / f"{uuid.uuid4()}.mp3"
    clip.write_bytes(b"x" * 10)
    return clip


async def test_a_class_deleted_while_it_rendered_stores_no_clip(tmp_path: Path) -> None:
    db = Db(None)
    kept = render_kind._Kept()  # pyright: ignore[reportPrivateUsage]
    store = render_kind._store(  # pyright: ignore[reportPrivateUsage]
        given(), clip_file(tmp_path), "cd" * 32, kept
    )
    with pytest.raises(JobFailure) as caught:
        await store(Fenced(db))  # pyright: ignore[reportArgumentType]
    assert caught.value.args == ("invalid_input",)
    assert db.seen == [render_kind._CLASS]  # pyright: ignore[reportPrivateUsage]
    assert kept.stored is False


async def test_a_class_still_there_stores_the_clip(tmp_path: Path) -> None:
    db = Db((1,))
    kept = render_kind._Kept()  # pyright: ignore[reportPrivateUsage]
    store = render_kind._store(  # pyright: ignore[reportPrivateUsage]
        given(), clip_file(tmp_path), "cd" * 32, kept
    )
    await store(Fenced(db))  # pyright: ignore[reportArgumentType]
    assert kept.stored is True
    assert render_kind._ADD in db.seen  # pyright: ignore[reportPrivateUsage]
