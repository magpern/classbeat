"""``player_events``, the outbox of signed events to the player (ADR 0034 section 2), as
SQLAlchemy Core.

A **system table** (ADR 0002 section 3): it has no ``owner_id`` and no user id, only the
pairwise ``sub``, so it survives the user's deletion and the deletion scan of ADR 0014 section 4
finds nothing of the user in it. Its own ``MetaData``; only ``classbeat.player.outbox`` (and
migrations and tests) may import this module or name the table in SQL
(``tests/test_player_events_guard.py``). Migration ``0051_player_events`` creates the same table;
``tests/test_player_events_schema.py`` keeps them in step.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

metadata = MetaData()

player_events = Table(
    "player_events",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("kind", Text, nullable=False),
    Column("sub", Text, nullable=False),
    Column("jti", Text, nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column(
        "give_up_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now() + interval '7 days'"),
    ),
    Column("state", Text, nullable=False, server_default=text("'pending'")),
    Column("attempt", Integer, nullable=False, server_default=text("0")),
    Column("available_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("lease_version", BigInteger, nullable=False, server_default=text("0")),
    Column("leased_by", Text),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("last_error", Text),
    Index("player_events_claim_idx", "available_at", postgresql_where=text("state = 'pending'")),
    Index("player_events_sub_idx", "sub"),
    Index("player_events_failed_idx", "give_up_at", postgresql_where=text("state = 'failed'")),
)

TABLE_NAME = player_events.name
