"""Passkeys: ``webauthn_credentials``, ``webauthn_challenges``, ``users.webauthn_user_handle``

Revision ID: 0041_passkeys
Revises: 0040_security_log_at_index
Create Date: 2026-11-07

ADR 0019 sections 6 and 7 (W1, #771). Two system tables in ``classbeat.auth`` (no ``owner_id``, no
RLS, guard 8), like ``identities`` and ``oauth_flows``: sign-in finds a credential by its id before
any user is known. ``classbeat.auth.tables`` holds the same definitions;
``tests/test_auth_schema.py`` keeps the two in step.

- ``webauthn_credentials``: one passkey (at most 10 per user, enforced by the routes under the
  user row lock). The credential id is unique across all users (WebAuthn's bound: 1023 bytes); the
  public key is the COSE key from registration (no secret); ``sign_count`` is WebAuthn's 32-bit
  counter; ``transports`` only the values the ADR keeps; ``backup_state`` cannot be set without
  ``backup_eligible`` (WebAuthn's flag rule); ``label`` 1 to 64 characters. Cascades with the user.
- ``webauthn_challenges``: one in-flight ceremony, found by the SHA-256 of the per-purpose flow
  cookie, 32 random challenge bytes, five minutes. ``sign_in`` has no user or session;
  ``register`` and ``reauth`` are bound to both, and go with either (cascade). The hourly purge
  deletes expired rows (``expires_at`` index).
- ``users.webauthn_user_handle``: 32 random bytes, unique, NULL until the first registration.

No grant: the default privileges of ``classbeat_owner`` give ``classbeat_app`` DML on each new
table (``docker/postgres/init/10-roles.sql``), and ``classbeat_maint``'s column grants
(``docker/postgres/maint/30-maint.sql``) are unchanged, so it reads none of this. The downgrade
drops both tables and the column (every passkey is lost; users keep their password or provider).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0041_passkeys"
down_revision: str | Sequence[str] | None = "0040_security_log_at_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRANSPORTS = "ARRAY['usb', 'nfc', 'ble', 'internal', 'hybrid', 'smart-card']::text[]"

STATEMENTS = (
    "ALTER TABLE public.users ADD COLUMN webauthn_user_handle bytea "
    "CONSTRAINT users_webauthn_user_handle_key UNIQUE "
    "CONSTRAINT users_webauthn_user_handle_check CHECK (octet_length(webauthn_user_handle) = 32)",
    f"""CREATE TABLE public.webauthn_credentials (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        credential_id bytea NOT NULL UNIQUE
            CONSTRAINT webauthn_credentials_id_length
            CHECK (octet_length(credential_id) BETWEEN 1 AND 1023),
        public_key bytea NOT NULL
            CONSTRAINT webauthn_credentials_key_length
            CHECK (octet_length(public_key) BETWEEN 1 AND 2048),
        sign_count bigint NOT NULL DEFAULT 0
            CONSTRAINT webauthn_credentials_sign_count_range
            CHECK (sign_count BETWEEN 0 AND 4294967295),
        transports text[] NOT NULL DEFAULT '{{}}'::text[]
            CONSTRAINT webauthn_credentials_transports_check CHECK (transports <@ {_TRANSPORTS}),
        backup_eligible boolean NOT NULL,
        backup_state boolean NOT NULL,
        label text NOT NULL
            CONSTRAINT webauthn_credentials_label_length CHECK (char_length(label) BETWEEN 1 AND 64),
        created_at timestamptz NOT NULL DEFAULT now(),
        last_used_at timestamptz,
        CONSTRAINT webauthn_credentials_backup_flags CHECK (backup_eligible OR NOT backup_state)
    )""",
    "CREATE INDEX webauthn_credentials_user_id_idx ON public.webauthn_credentials (user_id)",
    """CREATE TABLE public.webauthn_challenges (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        flow_hash bytea NOT NULL UNIQUE
            CONSTRAINT webauthn_challenges_flow_hash_length CHECK (octet_length(flow_hash) = 32),
        purpose text NOT NULL
            CONSTRAINT webauthn_challenges_purpose
            CHECK (purpose IN ('register', 'sign_in', 'reauth')),
        challenge bytea NOT NULL
            CONSTRAINT webauthn_challenges_challenge_length CHECK (octet_length(challenge) = 32),
        user_id uuid REFERENCES users (id) ON DELETE CASCADE,
        session_id uuid REFERENCES sessions (id) ON DELETE CASCADE,
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        CONSTRAINT webauthn_challenges_binding CHECK (
            (purpose = 'sign_in') = (user_id IS NULL) AND (user_id IS NULL) = (session_id IS NULL)
        )
    )""",
    "CREATE INDEX webauthn_challenges_session_id_idx ON public.webauthn_challenges (session_id)",
    "CREATE INDEX webauthn_challenges_user_id_idx ON public.webauthn_challenges (user_id)",
    "CREATE INDEX webauthn_challenges_expires_at_idx ON public.webauthn_challenges (expires_at)",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE public.webauthn_challenges")
    op.execute("DROP TABLE public.webauthn_credentials")
    op.execute("ALTER TABLE public.users DROP COLUMN webauthn_user_handle")
