"""Helpers for the ``keys rewrap`` and ``keys wipe`` tests: key files, users, rows, the CLI."""

import argparse
import io
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from authtools import Env, make_settings
from classbeat import connections
from classbeat.auth.keys import encrypt_totp_secret
from classbeat.auth.totp_keys import seal_totp_secret
from classbeat.cli import add_subcommands
from classbeat.config import Settings
from classbeat.crypto import keks
from classbeat.db import create_engine, create_session_factory, system_session, user_session
from classbeat.keys_cli import run
from ctx import make_context

KEY_2 = bytes(range(64, 96))
KEY_3 = bytes(range(128, 160))
TOTP_SECRET = b"JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
TOKENS = {
    "a@example.com": "aaaaaaaa-1111-2222-3333-444444444444",
    "b@example.com": "bbbbbbbb-1111-2222-3333-444444444444",
}


def parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_subcommands(parser)
    return parser.parse_args(argv)


async def cli(settings: Settings, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = await run(parse(*argv), settings, out, err)
    return code, out.getvalue(), err.getvalue()


def rotate(kek_dir: Path, new_id: str, new_key: bytes, old_id: str) -> None:
    """The rotation runbook: the old current file becomes ``connection_kek_<old id>``."""
    (kek_dir / "connection_kek").rename(kek_dir / f"connection_kek_{old_id}")
    (kek_dir / "connection_kek").write_bytes(new_key.hex().encode() + b"\n")


def settings_for(app_url: str, kek_id: str, **overrides: Any) -> Settings:
    return make_settings(app_url, connection_kek_id=kek_id, **overrides)


async def ring_for(settings: Settings) -> tuple[keks.KeyRing, Any]:
    """A fresh process's key ring (it records the check row) and its engine."""
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    return await keks.load_keyring(factory, settings.connection_kek_id), engine


async def connect(
    factory: async_sessionmaker[AsyncSession], ring: keks.KeyRing, user_id: UUID, token: str
) -> UUID:
    ctx = make_context(user_id)
    return await connections.save(factory, ring, ctx, "mowl", {"token": token})


async def token_of(
    factory: async_sessionmaker[AsyncSession], ring: keks.KeyRing, user_id: UUID
) -> str:
    opened = await connections.open_connection(factory, ring, make_context(user_id), "mowl")
    return opened.credentials.mowl_token


async def add_totp(
    env: Env, ring: keks.KeyRing, user_id: UUID, *, v1: bool = False, confirmed: bool = True
) -> None:
    """A confirmed TOTP row, in the v2 form (``ring``'s current KEK) or the ``app-v1`` form."""
    async with system_session(env.factory) as db:
        if v1:
            ciphertext, nonce, key_id = encrypt_totp_secret(
                env.app.state.keys, user_id, TOTP_SECRET
            )
            values = {
                "secret_ciphertext": ciphertext,
                "nonce": nonce,
                "key_id": key_id,
                "wrapped_dek": None,
                "dek_nonce": None,
            }
        else:
            values = await seal_totp_secret(db, ring, user_id, TOTP_SECRET)
        await db.execute(
            text(
                "INSERT INTO mfa_totp (user_id, secret_ciphertext, nonce, key_id, wrapped_dek, "
                "dek_nonce, confirmed_at) VALUES (:u, :secret_ciphertext, :nonce, :key_id, "
                ":wrapped_dek, :dek_nonce, CASE WHEN :c THEN now() END)"
            ),
            {"u": user_id, "c": confirmed, **values},
        )


async def owner_count(owner_factory: async_sessionmaker[AsyncSession], user_id: UUID) -> int:
    async with user_session(owner_factory, make_context(user_id)) as db:
        return (await db.execute(text("SELECT count(*) FROM connections"))).scalar_one()
