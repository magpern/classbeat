"""Database access. Nothing here connects or runs at import time."""

from classbeat.db.engine import (
    CONNECT_TIMEOUT_SECONDS,
    create_engine,
    create_migrations_engine,
    create_session_factory,
    event_loop_factory,
)
from classbeat.db.queue_check import queue_verified
from classbeat.db.role_check import DatabaseRoleError, verify_database_role
from classbeat.db.session import TransactionOwnedError, system_session, user_session
from classbeat.db.tables import SYSTEM_TABLES

__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "SYSTEM_TABLES",
    "DatabaseRoleError",
    "TransactionOwnedError",
    "create_engine",
    "create_migrations_engine",
    "create_session_factory",
    "event_loop_factory",
    "queue_verified",
    "system_session",
    "user_session",
    "verify_database_role",
]
