"""The classbeat-player connection (#1059, ADR 0034 section 3)

Revision ID: 0050_player_connection
Revises: 0049_oidc_provider
Create Date: 2026-11-15

- ``connections.provider`` gains ``player``: one row per user, under ADR 0004's envelope, whose
  secret is the player's ``cbp_src_`` source token.
- ``player_link_flows``: one in-flight link to the player (ADR 0034 section 3, modelled on
  ``spotify_oauth_flows`` of 0006). Owned, row security forced. ADR 0034 writes ``state_hash`` as
  ``UNIQUE``; guard 1 requires every unique index of an owned table to include ``owner_id``, so
  it is ``UNIQUE (owner_id, state_hash)`` as in 0006 (the take always names the owner and the
  session). The ``state`` and the session id are kept as SHA-256 only; the verifier is a
  one-time PKCE value, useless without the ``cbp_lc_`` code only the user's browser receives. A
  flow lives at most 10 minutes (a check).

The downgrade deletes every ``player`` connection (the old check refuses them) with row security
not forced for that one statement, as 0006's repairs do.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0050_player_connection"
down_revision: str | Sequence[str] | None = "0049_oidc_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_PROVIDERS = "'mowl', 'spotify', 'swedebeat'"
PROVIDERS = f"{OLD_PROVIDERS}, 'player'"
SHA256 = "octet_length({0}) = 32"

PLAYER_LINK_FLOWS = f"""
    CREATE TABLE public.player_link_flows (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        state_hash bytea NOT NULL
            CONSTRAINT player_link_flows_state_hash CHECK ({SHA256.format("state_hash")}),
        session_hash bytea NOT NULL
            CONSTRAINT player_link_flows_session_hash CHECK ({SHA256.format("session_hash")}),
        verifier text NOT NULL
            CONSTRAINT player_link_flows_verifier CHECK (verifier ~ '^[A-Za-z0-9_-]{{43}}$'),
        redirect_uri text NOT NULL CONSTRAINT player_link_flows_redirect_uri
            CHECK (char_length(redirect_uri) BETWEEN 1 AND 2048),
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        used_at timestamptz,
        CONSTRAINT player_link_flows_lifetime CHECK (
            expires_at > created_at AND expires_at <= created_at + interval '10 minutes'),
        CONSTRAINT player_link_flows_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT player_link_flows_owner_id_state_hash_key UNIQUE (owner_id, state_hash)
    )"""


def _provider_check(values: str) -> list[str]:
    return [
        "ALTER TABLE public.connections DROP CONSTRAINT connections_provider",
        "ALTER TABLE public.connections ADD CONSTRAINT connections_provider "
        f"CHECK (provider IN ({values}))",
    ]


def upgrade() -> None:
    for statement in _provider_check(PROVIDERS):
        op.execute(statement)
    op.execute(PLAYER_LINK_FLOWS)
    enable_owner_rls("player_link_flows")
    # The oldest of a user's flows (at most 3 unexpired) and the hourly purge.
    op.execute(
        "CREATE INDEX player_link_flows_owner_created "
        "ON public.player_link_flows (owner_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE public.player_link_flows")
    op.execute("ALTER TABLE public.connections NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM public.connections WHERE provider = 'player'")
    op.execute("ALTER TABLE public.connections FORCE ROW LEVEL SECURITY")
    for statement in _provider_check(OLD_PROVIDERS):
        op.execute(statement)
