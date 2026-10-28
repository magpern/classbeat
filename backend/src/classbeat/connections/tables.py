"""The ``connections`` table (ADR 0004 §1): owned, one row per user and provider.

Only ``classbeat.connections`` reads or writes it (``tests/test_crypto_guard.py``). Migration
``0003_connections`` creates it with its constraints and RLS; this is the query-side mirror.
"""

from sqlalchemy import Column, DateTime, ForeignKey, LargeBinary, MetaData, Table, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

PROVIDERS = ("mowl", "spotify", "swedebeat")
STATUSES = ("connected", "needs_reconnect")

metadata = MetaData()
connections = Table(
    "connections",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("owner_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("provider", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("last_error", Text),
    Column("public", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("secret", LargeBinary, nullable=False),
    Column("secret_nonce", LargeBinary, nullable=False),
    Column("wrapped_dek", LargeBinary, nullable=False),
    Column("dek_nonce", LargeBinary, nullable=False),
    Column("key_id", Text, nullable=False),
    Column("expires_at", DateTime(timezone=True)),
    Column("connected_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("last_used_at", DateTime(timezone=True)),
)
