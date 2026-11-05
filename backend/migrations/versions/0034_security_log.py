"""The privacy-safe security log (issue #632, ADR 0003 amendment "security log" of 2026-11-05)

Revision ID: 0034_security_log
Revises: 0033_session_client_mark
Create Date: 2026-11-05

``security_log``: one row per signed-in ``/api`` request, separate from the access log, for the
abuse view of a later PR. A row holds the user (``owner_id``), the session's id (no foreign key:
the session rotates and is deleted, the row stays until the purge), a keyed hash of the coarse
client network (``ip_hash``, HMAC under the ``security-log`` key of ``bucket_ip``), the method, the
route template (``/api/classes/{class_id}``, never the raw path or query), the status and the time.
Never an address, ``User-Agent``, token, query string or body; the checks below refuse what does
not look like a method, a template or a status.

Bounded in count, not only in age: every other request is within the general limit (3000 an hour
per user, a refused one included), and a 429 row carries its minute (``limited_minute``, epoch
minutes) under a partial unique index, so a session writes at most one 429 row a minute.

Owner RLS (ADR 0002); gone with the account (``ON DELETE CASCADE``) and purged after
``CLASSBEAT_SECURITY_LOG_DAYS`` (7 by default) by the worker's hourly purge, in each user's own
session. No grant to ``classbeat_maint`` yet (the admin view adds its own). The downgrade drops the
table.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0034_security_log"
down_revision: str | Sequence[str] | None = "0033_session_client_mark"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The route is a template of our own routes: a leading slash, lower-case path words, digits,
# ``_ . -`` and ``{name}`` placeholders; or the word ``unmatched``. No ``?``, ``%``, ``=``, ``&``,
# ``:``, ``@`` or space, so neither a query string nor an encoded value fits.
SECURITY_LOG = r"""
    CREATE TABLE public.security_log (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        session_id uuid NOT NULL,
        ip_hash bytea NOT NULL,
        method text NOT NULL,
        route text NOT NULL,
        status smallint NOT NULL,
        at timestamptz NOT NULL DEFAULT now(),
        limited_minute bigint,
        CONSTRAINT security_log_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT security_log_ip_hash CHECK (octet_length(ip_hash) = 32),
        CONSTRAINT security_log_method CHECK (
            method IN ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'OTHER')
        ),
        CONSTRAINT security_log_route CHECK (
            route = 'unmatched'
            OR (char_length(route) <= 200 AND route ~ '^(/([a-z0-9_.-]+|\{[a-z_][a-z0-9_]*\}))+$')
        ),
        CONSTRAINT security_log_status CHECK (status BETWEEN 100 AND 599),
        CONSTRAINT security_log_limited_minute CHECK (limited_minute IS NULL OR status = 429)
    )"""


def upgrade() -> None:
    op.execute(SECURITY_LOG)
    enable_owner_rls("security_log")
    op.execute("CREATE INDEX security_log_owner_at ON public.security_log (owner_id, at)")
    # At most one 429 row per session and minute: a script hammering past the limit adds no rows.
    op.execute(
        "CREATE UNIQUE INDEX security_log_one_429 ON public.security_log "
        "(owner_id, session_id, limited_minute) WHERE limited_minute IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE public.security_log")
