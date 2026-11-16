"""SwedeBeat studio: room for a full mix attempt in ``class_outputs.detail`` (ADR 0036 §6; SR C7b)

Revision ID: 0055_class_outputs_detail
Revises: 0054_class_outputs
Create Date: 2026-11-16

- ``class_outputs_detail``: ``octet_length(detail::text)`` at most 65536 bytes (was 32768). The
  bound is measured, not guessed (``classbeat.swedebeat_studio.outputs_rows.MAX_DETAIL_BYTES``
  says how): a mix attempt of 60 tracks with 64-character references stores its render plan, its
  analysed sha256s and a full 16 KB ``X-Tracks``, about 37 KB as ``jsonb`` text, before the later
  patches (the merge URL, a MOWL candidate); 32 KB refused it. The application refuses an attempt
  over the bound less the room its patches need before any write intent.

The downgrade restores 32768 after cutting what no longer fits: such a row's ``detail`` is
replaced by its ``name`` and ``category`` only, with row security lifted for that statement, as
0052 does (the attempt's operations keep their own record).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0055_class_outputs_detail"
down_revision: str | Sequence[str] | None = "0054_class_outputs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_MAX_DETAIL_BYTES = 32768
MAX_DETAIL_BYTES = 65536


def _check(limit: int) -> list[str]:
    return [
        "ALTER TABLE public.class_outputs DROP CONSTRAINT class_outputs_detail",
        "ALTER TABLE public.class_outputs ADD CONSTRAINT class_outputs_detail CHECK "
        f"(jsonb_typeof(detail) = 'object' AND octet_length(detail::text) <= {limit})",
    ]


def upgrade() -> None:
    for statement in _check(MAX_DETAIL_BYTES):
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE public.class_outputs NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE public.class_outputs SET detail = jsonb_strip_nulls(jsonb_build_object("
        "'name', left(detail->>'name', 200), 'category', left(detail->>'category', 64))) "
        f"WHERE octet_length(detail::text) > {OLD_MAX_DETAIL_BYTES}"
    )
    op.execute("ALTER TABLE public.class_outputs FORCE ROW LEVEL SECURITY")
    for statement in _check(OLD_MAX_DETAIL_BYTES):
        op.execute(statement)
