"""The SwedeBeat audition's clip service (ADR 0042 sections 6.3 to 6.5; CP-R2 #1394)

Revision ID: 0072_preview_clips
Revises: 0071_mix_not_applied
Create Date: 2026-11-21

Three owned tables, owner RLS forced, each with an owner-inclusive foreign key to its class
(``ON DELETE CASCADE``), so its rows go with the class and with the account:

- ``preview_sessions``: one listening session (Play by gesture). It lives 30 min
  (``expires_at <= created_at + 30 min``) and counts ``delivered_ms``, the length of each distinct
  clip served in it once (``delivered_keys``, the clips' keys), never more than 25 min of audio.
  Purged a day after its creation.
- ``preview_sources``: the per-owner source-track cache, one file per ``(owner_id, class_id,
  reference)`` with the SHA-256 and size of its bytes; the file is under the owner's own path on
  the private preview volume, named by the row's id.
- ``preview_clips``: rendered windows, **unique per ``(owner_id, key)``** (the content key alone
  would share a file across owners), ``[start_ms, end_ms)`` at most 6 min, the file's size and
  SHA-256, under the owner's own path. Sources and clips are purged after 14 idle days
  (``last_used_at``) and under a per-owner size cap.

``jobs`` gains ``preview_class_id`` for the ``preview_render`` job (amends ADR 0005 section 1, as
0052 and 0056 did): ``jobs_class_id`` allows a NULL ``class_id`` for ``preview_render`` too, so the
job is outside ``jobs_one_open_per_class`` and never makes the class ``class_busy``; instead it
names its class in ``preview_class_id``, an owner-inclusive foreign key to ``classes`` (``ON DELETE
CASCADE``: the job goes with the class). ``jobs_preview_class`` holds the shape: a
``preview_render`` job has no ``class_id`` and a ``preview_class_id``, and no other kind has a
``preview_class_id``.

The downgrade deletes the ``preview_render`` jobs (the old check refuses them) with row security
lifted for that statement, as 0052 does, and drops the column and the tables.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0072_preview_clips"
down_revision: str | Sequence[str] | None = "0071_mix_not_applied"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND = "preview_render"
# The check as migration 0058 left it (0052 made it, 0056 added ``spotify_sync``, 0058 the
# detached jobs; no other migration touches it).
OLD_CLASS_CHECK = (
    "class_id IS NOT NULL OR kind IN ('player_remove', 'spotify_sync') "
    "OR discarded_class_id IS NOT NULL"
)
CLASS_CHECK = (
    "class_id IS NOT NULL OR kind IN ('player_remove', 'spotify_sync', 'preview_render') "
    "OR discarded_class_id IS NOT NULL"
)
PREVIEW_CHECK = (
    f"(kind = '{KIND}') = (preview_class_id IS NOT NULL) "
    f"AND (kind <> '{KIND}' OR class_id IS NULL)"
)
LIFETIME_MINUTES = 30
DELIVERY_CAP_MS = 25 * 60 * 1000
MAX_COUNTED = 200
MAX_WINDOW_MS = 360000
MAX_SOURCE_BYTES = 60 * 1024 * 1024  # the gateway's download cap
MAX_CLIP_BYTES = 64 * 1024 * 1024  # 6 min at 320 kb/s is about 14.4 MB
KEYS = r"^([0-9a-f]{64}(,[0-9a-f]{64})*)?$"


def _class_fkey(table: str) -> str:
    return (
        f"CONSTRAINT {table}_class_fkey FOREIGN KEY (owner_id, class_id) "
        "REFERENCES public.classes (owner_id, id) ON DELETE CASCADE"
    )


PREVIEW_SESSIONS = f"""
    CREATE TABLE public.preview_sessions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        class_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        delivered_ms integer NOT NULL DEFAULT 0 CONSTRAINT preview_sessions_delivered_ms
            CHECK (delivered_ms >= 0 AND delivered_ms <= {DELIVERY_CAP_MS}),
        delivered_keys text[] NOT NULL DEFAULT '{{}}' CONSTRAINT preview_sessions_delivered_keys
            CHECK (cardinality(delivered_keys) <= {MAX_COUNTED}
                   AND array_to_string(delivered_keys, ',') ~ '{KEYS}'),
        CONSTRAINT preview_sessions_lifetime CHECK (expires_at > created_at
            AND expires_at <= created_at + make_interval(mins => {LIFETIME_MINUTES})),
        CONSTRAINT preview_sessions_owner_id_id_key UNIQUE (owner_id, id),
        {_class_fkey("preview_sessions")}
    )"""
PREVIEW_SOURCES = f"""
    CREATE TABLE public.preview_sources (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        class_id uuid NOT NULL,
        reference text NOT NULL
            CONSTRAINT preview_sources_reference CHECK (reference ~ '^[A-Za-z0-9_-]{{1,64}}$'),
        sha256 bytea NOT NULL CONSTRAINT preview_sources_sha256 CHECK (octet_length(sha256) = 32),
        bytes bigint NOT NULL
            CONSTRAINT preview_sources_bytes CHECK (bytes > 0 AND bytes <= {MAX_SOURCE_BYTES}),
        created_at timestamptz NOT NULL DEFAULT now(),
        last_used_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT preview_sources_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT preview_sources_owner_id_class_id_reference_key
            UNIQUE (owner_id, class_id, reference),
        {_class_fkey("preview_sources")}
    )"""
PREVIEW_CLIPS = f"""
    CREATE TABLE public.preview_clips (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        class_id uuid NOT NULL,
        key bytea NOT NULL CONSTRAINT preview_clips_key CHECK (octet_length(key) = 32),
        start_ms integer NOT NULL CONSTRAINT preview_clips_start_ms CHECK (start_ms >= 0),
        end_ms integer NOT NULL CONSTRAINT preview_clips_end_ms
            CHECK (end_ms > start_ms AND end_ms - start_ms <= {MAX_WINDOW_MS}),
        sha256 bytea NOT NULL CONSTRAINT preview_clips_sha256 CHECK (octet_length(sha256) = 32),
        bytes bigint NOT NULL
            CONSTRAINT preview_clips_bytes CHECK (bytes > 0 AND bytes <= {MAX_CLIP_BYTES}),
        created_at timestamptz NOT NULL DEFAULT now(),
        last_used_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT preview_clips_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT preview_clips_owner_id_key_key UNIQUE (owner_id, key),
        {_class_fkey("preview_clips")}
    )"""
INDEXES = (
    "CREATE INDEX preview_sessions_owner_created ON public.preview_sessions (owner_id, created_at)",
    "CREATE INDEX preview_sources_owner_used ON public.preview_sources (owner_id, last_used_at)",
    "CREATE INDEX preview_clips_owner_used ON public.preview_clips (owner_id, last_used_at)",
    "CREATE INDEX preview_clips_owner_class ON public.preview_clips (owner_id, class_id)",
    "CREATE INDEX jobs_owner_preview_class ON public.jobs (owner_id, preview_class_id) "
    "WHERE preview_class_id IS NOT NULL",
)


def _force_rls(forced: bool) -> str:
    return f"ALTER TABLE public.jobs {'FORCE' if forced else 'NO FORCE'} ROW LEVEL SECURITY"


def _class_check(expression: str) -> list[str]:
    return [
        "ALTER TABLE public.jobs DROP CONSTRAINT jobs_class_id",
        f"ALTER TABLE public.jobs ADD CONSTRAINT jobs_class_id CHECK ({expression})",
    ]


def upgrade() -> None:
    for statement, table in (
        (PREVIEW_SESSIONS, "preview_sessions"),
        (PREVIEW_SOURCES, "preview_sources"),
        (PREVIEW_CLIPS, "preview_clips"),
    ):
        op.execute(statement)
        enable_owner_rls(table)
    op.execute("ALTER TABLE public.jobs ADD COLUMN preview_class_id uuid")
    op.execute(
        "ALTER TABLE public.jobs ADD CONSTRAINT jobs_preview_class_fkey "
        "FOREIGN KEY (owner_id, preview_class_id) REFERENCES public.classes (owner_id, id) "
        "ON DELETE CASCADE"
    )
    op.execute(f"ALTER TABLE public.jobs ADD CONSTRAINT jobs_preview_class CHECK ({PREVIEW_CHECK})")
    for statement in _class_check(CLASS_CHECK):
        op.execute(statement)
    for statement in INDEXES:
        op.execute(statement)


def downgrade() -> None:
    op.execute(_force_rls(False))
    op.execute(f"DELETE FROM public.jobs WHERE kind = '{KIND}'")
    op.execute(_force_rls(True))
    for statement in _class_check(OLD_CLASS_CHECK):
        op.execute(statement)
    op.execute("DROP INDEX public.jobs_owner_preview_class")
    op.execute("ALTER TABLE public.jobs DROP CONSTRAINT jobs_preview_class")
    op.execute("ALTER TABLE public.jobs DROP CONSTRAINT jobs_preview_class_fkey")
    op.execute("ALTER TABLE public.jobs DROP COLUMN preview_class_id")
    for table in ("preview_clips", "preview_sources", "preview_sessions"):
        op.execute(f"DROP TABLE public.{table}")
