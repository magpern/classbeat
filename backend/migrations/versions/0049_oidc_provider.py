"""The OpenID Connect provider for classbeat-player (#1056, ADR 0034 section 1)

Revision ID: 0049_oidc_provider
Revises: 0048_paddle_links
Create Date: 2026-11-15

Three system tables, reached only through ``classbeat.auth.oidc`` (guard 8); the app role gets
the default DML. ``classbeat.auth.oidc.schema`` mirrors them (``tests/test_oidc_schema.py``).

- ``oidc_requests``: one pending authorization, bound to the browser by the SHA-256 of its
  ``__Host-cb_oidc`` cookie; ten minutes. No user column: it exists before anyone signs in.
- ``oidc_codes``: a code issued to the client (SHA-256 only), single use, 60 seconds; goes with
  the account.
- ``oidc_grants``: one row per user and client: the consent (``consent_at``, null after a
  withdrawal) and the first token issued (``first_issued_at``); goes with the account.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0049_oidc_provider"
down_revision: str | Sequence[str] | None = "0048_paddle_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLIENT = "{0} ~ '^[A-Za-z0-9._-]{{1,128}}$'"
SHORT = "char_length({0}) BETWEEN 1 AND 512"
CHALLENGE = "{0} ~ '^[A-Za-z0-9_-]{{43}}$'"

UP = (
    f"""
    CREATE TABLE public.oidc_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        browser_hash bytea NOT NULL
            CONSTRAINT oidc_requests_browser_hash CHECK (octet_length(browser_hash) = 32),
        redirect_uri text NOT NULL
            CONSTRAINT oidc_requests_redirect_uri CHECK ({SHORT.format("redirect_uri")}),
        state text NOT NULL CONSTRAINT oidc_requests_state CHECK ({SHORT.format("state")}),
        nonce text NOT NULL CONSTRAINT oidc_requests_nonce CHECK ({SHORT.format("nonce")}),
        code_challenge text NOT NULL
            CONSTRAINT oidc_requests_code_challenge CHECK ({CHALLENGE.format("code_challenge")}),
        prompt_login boolean NOT NULL,
        received_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        CONSTRAINT oidc_requests_lifetime CHECK (expires_at > received_at)
    )""",
    "CREATE INDEX oidc_requests_browser_idx ON public.oidc_requests (browser_hash, received_at)",
    "CREATE INDEX oidc_requests_expires_idx ON public.oidc_requests (expires_at)",
    f"""
    CREATE TABLE public.oidc_codes (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        code_hash bytea NOT NULL UNIQUE
            CONSTRAINT oidc_codes_code_hash CHECK (octet_length(code_hash) = 32),
        user_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        client_id text NOT NULL CONSTRAINT oidc_codes_client_id CHECK ({CLIENT.format("client_id")}),
        redirect_uri text NOT NULL
            CONSTRAINT oidc_codes_redirect_uri CHECK ({SHORT.format("redirect_uri")}),
        code_challenge text NOT NULL
            CONSTRAINT oidc_codes_code_challenge CHECK ({CHALLENGE.format("code_challenge")}),
        nonce text NOT NULL CONSTRAINT oidc_codes_nonce CHECK ({SHORT.format("nonce")}),
        auth_time timestamptz NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        used_at timestamptz,
        CONSTRAINT oidc_codes_lifetime CHECK (expires_at > created_at)
    )""",
    "CREATE INDEX oidc_codes_user_id_idx ON public.oidc_codes (user_id)",
    "CREATE INDEX oidc_codes_expires_idx ON public.oidc_codes (expires_at)",
    f"""
    CREATE TABLE public.oidc_grants (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        client_id text NOT NULL
            CONSTRAINT oidc_grants_client_id CHECK ({CLIENT.format("client_id")}),
        consent_at timestamptz,
        first_issued_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT oidc_grants_user_client_key UNIQUE (user_id, client_id)
    )""",
)

DOWN = (
    "DROP TABLE public.oidc_grants",
    "DROP TABLE public.oidc_codes",
    "DROP TABLE public.oidc_requests",
)


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
