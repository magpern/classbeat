"""SR C7b (ADR 0036 section 6): ``class_outputs.detail`` holds the largest attempt the policies can
build, measured as Postgres measures it (``octet_length(detail::text)``, migration 0055).

- the worst case is a mix of ``SIZED_TRACKS`` (60) tracks with 64-character references, its render
  plan and analysed sha256s and a full 16 KB X-Tracks, built by the real policies; it fits the
  bound ``open_attempt`` takes (``MAX_DETAIL_BYTES - PATCH_RESERVE``) and its later patches fit
  ``PATCH_RESERVE``; 32 KB (migration 0054) would have refused it;
- ``detail_bytes`` is exactly what the check measures, and the check takes ``MAX_DETAIL_BYTES``
  bytes and refuses one more (against Postgres);
- ``open_attempt`` refuses a larger detail with ``invalid_input`` before anything is written.
"""

import json
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from classbeat.jobs import JobFailure
from classbeat.studio import output_body_policy as body
from classbeat.swedebeat_gateway import write_policy as wp
from classbeat.swedebeat_studio import outputs_rows
from classbeat.swedebeat_studio.outputs_rows import (
    MAX_DETAIL_BYTES,
    PATCH_RESERVE,
    SIZED_TRACKS,
    detail_bytes,
)
from studiotools import StudioEnv
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    designed,
)

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default")
OLD_MAX_DETAIL_BYTES = 32768
LONG_URL = "https://" + "h" * (wp.MAX_URL - len("https://") - 6) + "/x.mp3"


def _references() -> list[str]:
    return [f"{n:02d}" + "r" * 62 for n in range(SIZED_TRACKS)]


def _plan(references: list[str]) -> list[dict[str, Any]]:
    spec = {
        "tracks": [{"reference": r, "start_sec": 0.5, "end_sec": 3599.5} for r in references],
        "crossfades": [12] * (len(references) - 1),
    }
    return body.render_plan(spec, dict.fromkeys(references, 3600.0))


def _full_x_tracks(plan: list[dict[str, Any]], references: list[str]) -> list[dict[str, Any]]:
    """The longest titles and artists whose X-Tracks still fit 16 KB uncut: a full header."""
    best: list[dict[str, Any]] = []
    for length in range(1, body.TEXT_MAX + 1):
        facts = {r: body.Facts("é" * length, "ü" * length, 199.99) for r in references}
        items = body.x_tracks(plan, facts)
        if items[0]["title"] != "é" * length:  # cut: this length no longer fits
            break
        best = items
    return best


def worst_mix_detail() -> dict[str, Any]:
    references = _references()
    plan = _plan(references)
    x_tracks = _full_x_tracks(plan, references)
    assert len(wp.x_tracks_header(x_tracks)) > wp.X_TRACKS_MAX - 200  # full, to the byte or so
    return {
        "name": body.mix_name("Ü" * 500, body.new_suffix()),
        "plan": plan,
        "sha256s": {r: "f" * 64 for r in references},
        "x_tracks": x_tracks,
        "duration_sec": 215999.123456,
    }


def worst_patches() -> dict[str, Any]:
    """Everything a mix attempt's row gains after it is opened, at its longest."""
    return {
        "render_sha256": "a" * 64,
        "mix_url": LONG_URL,
        "candidate": {"name": "𝄞" * 200, "url": LONG_URL, "duration_sec": 215999.123456},
        "confirmed": True,
        "superseded": True,
        "published": True,
        "playlist_id": "p" * 64,
        "mowl_playlist_id": 2**63 - 1,
        "category": "render_not_reproducible",
    }


def test_the_largest_mix_attempt_fits_with_its_patches() -> None:
    detail = worst_mix_detail()
    measured = detail_bytes(detail)
    assert OLD_MAX_DETAIL_BYTES < measured <= MAX_DETAIL_BYTES - PATCH_RESERVE
    assert detail_bytes(worst_patches()) <= PATCH_RESERVE
    assert detail_bytes({**detail, **worst_patches()}) <= MAX_DETAIL_BYTES


async def test_a_larger_detail_is_refused_before_anything_is_written() -> None:
    detail = {"x": "y" * (MAX_DETAIL_BYTES - PATCH_RESERVE)}
    with pytest.raises(JobFailure) as refused:  # the database is never reached
        await outputs_rows.open_attempt(
            cast(Any, None), uuid4(), uuid4(), uuid4(), "swedebeat_mix", 1, detail
        )
    assert refused.value.category == "invalid_input"


async def test_the_check_measures_what_detail_bytes_counts_to_the_byte(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio)
    owner, revision = rider.user_id, current.current_revision_id
    sql = studio.songs.sql

    def insert(detail: dict[str, Any], attempt: int) -> UUID:
        return sql(
            "INSERT INTO class_outputs (owner_id, class_id, revision_id, output, attempt, state, "
            "detail) VALUES (:o, :c, :r, 'swedebeat_mix', :a, 'pending', CAST(:d AS jsonb)) "
            "RETURNING id",
            o=owner, c=class_id, r=revision, a=attempt, d=json.dumps(detail),
        ).scalar_one()  # fmt: skip

    full = {**worst_mix_detail(), **worst_patches()}
    row_id = insert(full, 1)
    stored = sql("SELECT octet_length(detail::text) FROM class_outputs WHERE id = :i", i=row_id)
    assert stored.scalar_one() == detail_bytes(full)
    pad = MAX_DETAIL_BYTES - detail_bytes({"x": ""})
    insert({"x": "y" * pad}, 2)  # exactly the bound
    with pytest.raises(IntegrityError, match="class_outputs_detail"):
        insert({"x": "y" * (pad + 1)}, 3)
