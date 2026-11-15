"""The OpenID Connect provider's system tables (ADR 0034 section 1), as SQLAlchemy Core.

They have their own ``MetaData``: importing ``classbeat.auth.tables`` stays free of the provider.
Only ``classbeat.auth.oidc`` (and migrations and tests) may import this module or name these
tables in SQL; guard 8 (``tests/auth_tables_scan.py``) lists them. Migration
``0049_oidc_provider`` creates the same tables; ``tests/test_oidc_schema.py`` keeps them in step.
"""

from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

metadata = MetaData()
# ``users`` lives on ``classbeat.auth.tables.metadata``; a stub here lets the foreign keys resolve.
_users = Table("users", metadata, Column("id", UUID(as_uuid=True), primary_key=True))


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> Column[Any]:
    default = text("now()") if default_now else None
    return Column(name, DateTime(timezone=True), nullable=nullable, server_default=default)


def _user_fk() -> Column[Any]:
    return Column(
        "user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


# One pending authorization, bound to the browser by the hash of its ``__Host-cb_oidc`` cookie.
oidc_requests = Table(
    "oidc_requests",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("browser_hash", LargeBinary, nullable=False),
    Column("redirect_uri", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("nonce", Text, nullable=False),
    Column("code_challenge", Text, nullable=False),
    Column("prompt_login", Boolean, nullable=False),
    _ts("received_at", nullable=False, default_now=True),
    _ts("expires_at", nullable=False),
    Index("oidc_requests_browser_idx", "browser_hash", "received_at"),
    Index("oidc_requests_expires_idx", "expires_at"),
)

# A code issued to the client: single use, 60 seconds.
oidc_codes = Table(
    "oidc_codes",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("code_hash", LargeBinary, nullable=False, unique=True),
    _user_fk(),
    Column("client_id", Text, nullable=False),
    Column("redirect_uri", Text, nullable=False),
    Column("code_challenge", Text, nullable=False),
    Column("nonce", Text, nullable=False),
    _ts("auth_time", nullable=False),
    _ts("created_at", nullable=False, default_now=True),
    _ts("expires_at", nullable=False),
    _ts("used_at"),
    Index("oidc_codes_user_id_idx", "user_id"),
    Index("oidc_codes_expires_idx", "expires_at"),
)

# One row per user and client: the consent, and the durable "has a player account" marker.
oidc_grants = Table(
    "oidc_grants",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    _user_fk(),
    Column("client_id", Text, nullable=False),
    _ts("consent_at"),
    _ts("first_issued_at"),
    _ts("created_at", nullable=False, default_now=True),
    UniqueConstraint("user_id", "client_id", name="oidc_grants_user_client_key"),
)

TABLES = (oidc_requests, oidc_codes, oidc_grants)
TABLE_NAMES = tuple(table.name for table in TABLES)
