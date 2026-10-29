"""Key-encryption keys: the KEK files and the ``connection_keks`` check (ADR 0004 §2).

- The current KEK is the secret file ``connection_kek``: exactly 64 lowercase hexadecimal
  characters (32 bytes), optionally followed by one newline. Its id is
  ``CLASSBEAT_CONNECTION_KEK_ID`` (default ``kek-1``, pattern ``^kek-[a-z0-9-]{1,28}$``).
- Retired KEKs are files ``connection_kek_<key_id>``, read for decryption only. Only a row's
  ``key_id`` that matches the pattern names such a file; no other input builds a file name.
- ``connection_keks`` (a system table, reached only through this module) holds
  ``HMAC-SHA256(KEK, "classbeat-kek-check-v1")[:16]`` per key id. On startup web and worker
  insert the current KEK's row if missing and compare it, so a key swapped under an unchanged id
  refuses to start. A retired file with no row, or a different value, is never loaded
  (``KeyUnavailable``). Negative results are not cached: restoring the file fixes it.

Key material is never logged and never in an exception message.
"""

import hmac
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import NoReturn

from sqlalchemy import (
    Column,
    DateTime,
    LargeBinary,
    MetaData,
    Table,
    Text,
    delete,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from classbeat.config import secrets_dir
from classbeat.crypto.envelope import Kek, check_value, is_key_id
from classbeat.db import system_session

logger = logging.getLogger(__name__)

CURRENT_FILE = "connection_kek"
RETIRED_PREFIX = "connection_kek_"
DEFAULT_KEY_ID = "kek-1"
_FILE_CONTENT = re.compile(rb"[0-9a-f]{64}\n?")

metadata = MetaData()
connection_keks = Table(
    "connection_keks",
    metadata,
    Column("key_id", Text, primary_key=True),
    Column("check_value", LargeBinary, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


class KekError(RuntimeError):
    """Web and worker refuse to start: the current KEK is missing, malformed or swapped."""


class KeyUnavailable(Exception):
    """The KEK a row names is missing, malformed or does not match its check row."""

    def __init__(self) -> None:
        super().__init__("key_unavailable")


class _Malformed(Exception):
    pass


def _read_key(path: Path) -> bytes | None:
    """The 32 key bytes of ``path``; ``None`` when there is no such file. ``_Malformed``
    otherwise (never says what the file holds)."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise _Malformed from None
    if _FILE_CONTENT.fullmatch(raw) is None:
        raise _Malformed
    return bytes.fromhex(raw[:64].decode("ascii"))


class KeyRing:
    """The current KEK, and retired ones loaded on demand. ``repr`` shows ids only.

    The current KEK is used only once its check row matched (``verified``). Normally that
    happens at startup; when the database was unreachable then, the first use checks it, and a
    mismatch refuses every use (``KekError``), the same as a refused start. That check runs in
    its own ``system_session`` (``factory``, required when ``verified`` is false), so a caller's
    transaction that rolls back afterwards can never leave the ring marked verified while the
    check row was never committed.
    """

    def __init__(
        self,
        current: Kek,
        directory: Path,
        *,
        verified: bool = True,
        factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        if not verified and factory is None:
            raise ValueError("an unverified KeyRing needs a session factory for its first check")
        self._current = current
        self._directory = directory
        self._factory = factory
        self._retired: dict[str, Kek] = {}
        self._verified = verified

    @property
    def current_id(self) -> str:
        return self._current.key_id

    @property
    def verified(self) -> bool:
        return self._verified

    def __repr__(self) -> str:
        return f"KeyRing(current={self._current.key_id!r})"

    def __reduce__(self) -> NoReturn:
        raise TypeError("a KeyRing cannot be pickled")

    async def current(self, db: AsyncSession) -> Kek:
        """The current KEK, for writes. Checked against its row first if not done yet (in a
        session of its own; ``db`` is not used for it)."""
        if not self._verified:
            assert self._factory is not None  # guaranteed by __init__
            async with system_session(self._factory) as own:
                await _record_and_compare(own, self._current)
            self._verified = True  # only after the check row's transaction committed
        return self._current

    async def kek_for(self, db: AsyncSession, key_id: str) -> Kek:
        """The KEK named ``key_id`` (a row's column), or ``KeyUnavailable``."""
        if key_id == self._current.key_id:
            return await self.current(db)
        if not is_key_id(key_id):
            raise KeyUnavailable
        cached = self._retired.get(key_id)
        if cached is not None:
            return cached
        key = self._load_retired(key_id)
        if key is None:
            raise KeyUnavailable
        stored = await stored_check_value(db, key_id)
        if stored is None or not hmac.compare_digest(stored, check_value(key)):
            raise KeyUnavailable
        kek = Kek(key_id, key)
        self._retired[key_id] = kek
        return kek

    def _load_retired(self, key_id: str) -> bytes | None:
        try:
            return _read_key(self._directory / f"{RETIRED_PREFIX}{key_id}")
        except _Malformed:
            return None


async def _record_and_compare(db: AsyncSession, kek: Kek) -> None:
    """Record the KEK's check value if its id has no row yet (whoever comes first), read the
    row back and compare. ``KekError`` on a mismatch: a different key under an unchanged id."""
    expected = check_value(kek.key)
    await db.execute(
        pg_insert(connection_keks)
        .values(key_id=kek.key_id, check_value=expected)
        .on_conflict_do_nothing(index_elements=[connection_keks.c.key_id])
    )
    stored = await stored_check_value(db, kek.key_id)
    if stored is None or not hmac.compare_digest(stored, expected):
        raise KekError(
            "the connection_kek file does not match the check value recorded for "
            "its key id (a different key under an unchanged id); refusing to start"
        )


async def stored_check_value(db: AsyncSession, key_id: str) -> bytes | None:
    value = (
        await db.execute(
            select(connection_keks.c.check_value).where(connection_keks.c.key_id == key_id)
        )
    ).scalar_one_or_none()
    return None if value is None else bytes(value)


def read_current(key_id: str, directory: Path | None = None) -> Kek:
    """The current KEK from ``connection_kek``, or ``KekError``."""
    if not is_key_id(key_id):
        raise KekError("CLASSBEAT_CONNECTION_KEK_ID does not match ^kek-[a-z0-9-]{1,28}$")
    path = (directory or secrets_dir()) / CURRENT_FILE
    try:
        key = _read_key(path)
    except _Malformed:
        raise KekError(
            "the connection_kek secret file must hold exactly 64 lowercase hexadecimal "
            "characters, optionally followed by one newline"
        ) from None
    if key is None:
        raise KekError(
            "the connection_kek secret file is missing (scripts/worktree.sh init creates it; "
            "see ADR 0004 section 2)"
        )
    return Kek(key_id, key)


async def load_keyring(
    factory: async_sessionmaker[AsyncSession], key_id: str, directory: Path | None = None
) -> KeyRing:
    """Read the current KEK (``KekError`` when missing or malformed) and run the startup check
    of its check row (``KekError`` on a mismatch). A database that is unreachable at startup is
    not an error here, as for the role check: the first use runs the check instead."""
    directory = directory or secrets_dir()
    kek = read_current(key_id, directory)
    try:
        async with system_session(factory) as db:
            await _record_and_compare(db, kek)
    except OperationalError:
        logger.debug("database unreachable at startup; the KEK is checked on first use")
        return KeyRing(kek, directory, verified=False, factory=factory)
    return KeyRing(kek, directory)


async def has_check_row(db: AsyncSession, key_id: str) -> bool:
    """For ``keys wipe``: has this key id been used already (so the leaked key is still it)?"""
    return await stored_check_value(db, key_id) is not None


async def check_rows(db: AsyncSession) -> list[tuple[str, datetime]]:
    rows = await db.execute(
        select(connection_keks.c.key_id, connection_keks.c.created_at).order_by(
            connection_keks.c.created_at
        )
    )
    return [(row.key_id, row.created_at) for row in rows]


async def delete_check_rows(db: AsyncSession) -> int:
    """For ``keys wipe``: forget every KEK. Returns how many rows went."""
    result = await db.execute(delete(connection_keks).returning(connection_keks.c.key_id))
    return len(result.all())


async def vacuum_table(connection: AsyncConnection) -> None:
    """``keys wipe``: ``VACUUM (FULL)`` of the check table (autocommit, owner role)."""
    await connection.execute(text("VACUUM (FULL) public.connection_keks"))
