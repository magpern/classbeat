"""provider connections and envelope encryption (ADR 0004)

Revision ID: 0003_connections
Revises: 0002_auth
Create Date: 2026-10-28

- ``connections``: the first owned table (ADR 0002 section 2): ``owner_id`` referencing
  ``users``, ``UNIQUE (owner_id, id)``, every other unique constraint including ``owner_id``, RLS
  enabled and forced with the one ``owner_isolation`` policy. One row per user and provider; the
  payload is AES-256-GCM under a per-write data key, wrapped by the KEK named in ``key_id``.
- ``connection_keks``: a system table holding the check value of each KEK id (reached only
  through ``classbeat.crypto.keks``).
- ``mfa_totp`` gets ``wrapped_dek`` and ``dek_nonce`` for the v2 (envelope) form, and a check
  that tells ``app-v1`` rows (ADR 0003 section 8) from v2 rows apart. The v2 branch also says
  ``IS NOT NULL``: ``octet_length(NULL) = 48`` is NULL, and a CHECK passes on NULL, so without
  it a ``kek-`` row with no wrapped key would be accepted.

The default privileges of ``classbeat_owner`` give ``classbeat_app`` DML on both new tables.
This migration moves no data (``python -m classbeat keys rewrap --totp`` does, per row, as the
app role), so it needs no RLS bypass.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0003_connections"
down_revision: str | Sequence[str] | None = "0002_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY_ID = "'^kek-[a-z0-9-]{1,28}$'"
CATEGORIES = (
    "'invalid_credentials', 'needs_reconnect', 'rate_limited', 'upstream_unavailable', "
    "'key_unavailable', 'decrypt_failed', 'not_connected', 'unknown'"
)

CONNECTIONS = f"""
    CREATE TABLE public.connections (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        provider text NOT NULL
            CONSTRAINT connections_provider CHECK (provider IN ('mowl', 'spotify', 'swedebeat')),
        status text NOT NULL
            CONSTRAINT connections_status CHECK (status IN ('connected', 'needs_reconnect')),
        last_error text CONSTRAINT connections_last_error CHECK (last_error IN ({CATEGORIES})),
        public jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        secret bytea NOT NULL,
        secret_nonce bytea NOT NULL
            CONSTRAINT connections_secret_nonce CHECK (octet_length(secret_nonce) = 12),
        wrapped_dek bytea NOT NULL
            CONSTRAINT connections_wrapped_dek CHECK (octet_length(wrapped_dek) = 48),
        dek_nonce bytea NOT NULL
            CONSTRAINT connections_dek_nonce CHECK (octet_length(dek_nonce) = 12),
        key_id text NOT NULL CONSTRAINT connections_key_id CHECK (key_id ~ {KEY_ID}),
        expires_at timestamptz,
        connected_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        last_used_at timestamptz,
        CONSTRAINT connections_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT connections_owner_id_provider_key UNIQUE (owner_id, provider)
    )"""

CONNECTION_KEKS = f"""
    CREATE TABLE public.connection_keks (
        key_id text PRIMARY KEY CONSTRAINT connection_keks_key_id CHECK (key_id ~ {KEY_ID}),
        check_value bytea NOT NULL
            CONSTRAINT connection_keks_check_value CHECK (octet_length(check_value) = 16),
        created_at timestamptz NOT NULL DEFAULT now()
    )"""

TOTP_FORMS = f"""
    ALTER TABLE public.mfa_totp
        ADD COLUMN wrapped_dek bytea,
        ADD COLUMN dek_nonce bytea,
        ADD CONSTRAINT mfa_totp_key_form CHECK (
            (key_id = 'app-v1' AND wrapped_dek IS NULL AND dek_nonce IS NULL)
            OR (key_id ~ {KEY_ID} AND wrapped_dek IS NOT NULL AND dek_nonce IS NOT NULL
                AND octet_length(wrapped_dek) = 48 AND octet_length(dek_nonce) = 12))"""


def upgrade() -> None:
    op.execute(CONNECTIONS)
    enable_owner_rls("connections")
    op.execute(CONNECTION_KEKS)
    op.execute(TOTP_FORMS)


def downgrade() -> None:
    # v2 TOTP secrets cannot be read without these columns: refuse rather than lose them.
    op.execute(
        """DO $$ BEGIN
             IF EXISTS (SELECT FROM public.mfa_totp WHERE key_id <> 'app-v1') THEN
               RAISE EXCEPTION 'mfa_totp holds v2 (envelope) secrets; downgrade refused';
             END IF;
           END $$"""
    )
    op.execute("ALTER TABLE public.mfa_totp DROP CONSTRAINT mfa_totp_key_form")
    op.execute("ALTER TABLE public.mfa_totp DROP COLUMN wrapped_dek, DROP COLUMN dek_nonce")
    op.execute("DROP TABLE IF EXISTS public.connection_keks")
    op.execute("DROP TABLE IF EXISTS public.connections")
