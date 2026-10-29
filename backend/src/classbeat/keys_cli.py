"""``python -m classbeat keys rewrap|wipe`` (ADR 0004 section 2). Prints counts only.

``keys rewrap`` runs as the app role, like the other operator commands (``docker compose exec web
...``): one context per user from ``classbeat.auth``, ``app.user_id`` per transaction, one row per
transaction. ``keys rewrap --totp`` migrates the ``app-v1`` TOTP secrets to the v2 form once.

``keys wipe`` (after a suspected KEK compromise) runs only in the ``maint`` compose service, as
the owner role (web and worker never get the owner password)::

    docker compose --profile maint run --rm maint python -m classbeat keys wipe --confirm

It refuses while any session of the app role is connected, or when the configured KEK id already
has a check row (the leaked key was not replaced). Per user, in one transaction with
``app.user_id`` set (``SET LOCAL`` through ``set_config(..., true)``; the owner's pool runs no
``DISCARD ALL``): delete the connections, write ``connections_wiped`` and reset the TOTP if any;
after the commit, mail the user. Then it deletes every KEK check row, runs ``VACUUM (FULL)`` and
``CHECKPOINT``. ``CHECKPOINT`` needs a superuser (or ``pg_checkpoint``, which the owner role is
never given, ADR 0002 section 4): when refused, run it yourself as printed. ``--resend`` only
re-sends the unmarked mails (it skips both refusals). Exit codes: 0 when every user was
processed (``--resend``: every mail sent), else 1.
"""

import argparse
from collections import Counter
from typing import Any, TextIO

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from classbeat import auth, connections
from classbeat.auth import Keys
from classbeat.config import Settings
from classbeat.crypto import keks
from classbeat.db import (
    create_engine,
    create_migrations_engine,
    create_session_factory,
    system_session,
    user_session,
    verify_database_role,
)
from classbeat.mail import Mailer, MailError

CHECKPOINT_STEP = "docker compose exec db psql -U postgres -d classbeat -c CHECKPOINT"
Factory = async_sessionmaker[AsyncSession]


def add_keys_subcommands(sub: Any) -> None:
    """``keys rewrap [--totp]`` and ``keys wipe --confirm | --resend`` (``sub``: the subparsers)."""
    keys = sub.add_parser(
        "keys", help="rotate or wipe the connection keys (ADR 0004)"
    ).add_subparsers(dest="action", required=True)
    rewrap = keys.add_parser(
        "rewrap", help="move every connection and v2 TOTP secret onto the current KEK"
    )
    rewrap.add_argument(
        "--totp", action="store_true", help="once: migrate app-v1 TOTP secrets to the v2 form"
    )
    wipe = keys.add_parser(
        "wipe",
        help="after a suspected KEK compromise: delete every connection and reset every TOTP "
        "(maint service only)",
        description=(
            "Run in the maint service with web and worker stopped: docker compose --profile "
            "maint run --rm maint python -m classbeat keys wipe --confirm. If CHECKPOINT is "
            f"refused (the owner role may not run it), run: {CHECKPOINT_STEP}"
        ),
    )
    mode = wipe.add_mutually_exclusive_group(required=True)
    mode.add_argument("--confirm", action="store_true", help="really wipe")
    mode.add_argument("--resend", action="store_true", help="re-send the unsent wipe mails")


def _print_counts(title: str, counts: Counter[str], out: TextIO) -> None:
    print(title, file=out)
    if not counts:
        print("  (none)", file=out)
    for key in sorted(counts):
        print(f"  {key}: {counts[key]}", file=out)


_OK_OUTCOMES = (" rewrapped", " migrated", " skipped")


def _failures(counts: Counter[str]) -> int:
    """Rows whose outcome is not a move or a harmless skip (``decrypt_failed``,
    ``key_unavailable``): the operator must look at them, so the command exits non-zero."""
    return sum(n for key, n in counts.items() if not key.endswith(_OK_OUTCOMES))


async def rewrap(args: argparse.Namespace, settings: Settings, out: TextIO, err: TextIO) -> int:
    engine = create_engine(settings)
    try:
        await verify_database_role(engine)
        factory = create_session_factory(engine)
        ring = await keks.load_keyring(factory, settings.connection_kek_id)
        migrated: Counter[str] = Counter()
        if args.totp:
            migrated = await auth.migrate_v1_secrets(factory, Keys.from_settings(settings), ring)
            _print_counts("TOTP app-v1 -> v2:", migrated, out)
        async with system_session(factory) as db:
            contexts = await auth.operator_contexts(db)
        moved: Counter[str] = Counter()
        remaining: Counter[str] = Counter()
        for ctx in contexts:
            moved += await connections.rewrap_owner(factory, ring, ctx)
            remaining += await connections.rows_by_key(factory, ctx)
        _print_counts("connections rewrapped (old key_id outcome):", moved, out)
        totp_moved = await auth.rewrap_v2_secrets(factory, ring)
        _print_counts("TOTP rewrapped (old key_id outcome):", totp_moved, out)
        _print_counts("connections per key_id now:", remaining, out)
        _print_counts("TOTP per key_id now:", await auth.totp_rows_by_key(factory), out)
        failed = _failures(moved)
        if failed:
            print(f"{failed} connection rows could not be rewrapped", file=err)
        totp_failed = _failures(migrated) + _failures(totp_moved)
        if totp_failed:
            print(f"{totp_failed} TOTP rows could not be migrated or rewrapped", file=err)
        return 1 if failed or totp_failed else 0
    finally:
        await engine.dispose()


async def _app_sessions(db: AsyncSession, role: str) -> int:
    return (
        await db.execute(
            text("SELECT count(*) FROM pg_stat_activity WHERE usename = :role"), {"role": role}
        )
    ).scalar_one()


async def _send(settings: Settings, factory: Factory, event: auth.WipeEvent) -> bool:
    """Build the mail from the event, send it, mark the event. False when sending failed."""
    async with system_session(factory) as db:
        message = await auth.wipe_mail(settings, db, event)
    try:
        await Mailer(settings).send(message)
    except MailError:
        return False
    async with system_session(factory) as db:
        await auth.mark_wipe_mailed(db, event.event_id)
    return True


async def resend(settings: Settings, factory: Factory, out: TextIO) -> int:
    async with system_session(factory) as db:
        pending = await auth.pending_wipe_mails(db)
    sent = 0
    for event in pending:
        sent += await _send(settings, factory, event)
    unsent = len(pending) - sent
    print(f"wipe mails: {sent} sent, {unsent} unsent", file=out)
    return 1 if unsent else 0


async def _refusal(settings: Settings, factory: Factory) -> str | None:
    app_role = settings.sqlalchemy_url().username or "classbeat_app"
    async with system_session(factory) as db:
        if await _app_sessions(db, app_role):
            return "the app role is still connected (stop web and worker first)"
        if await keks.has_check_row(db, settings.connection_kek_id):
            return (
                "the configured CLASSBEAT_CONNECTION_KEK_ID already has a check row: install a "
                "new KEK under a new id first"
            )
    return None


async def _finish(engine: AsyncEngine, out: TextIO) -> None:
    """Forget every KEK, rewrite the affected tables, then try a checkpoint."""
    async with system_session(create_session_factory(engine)) as db:
        forgotten = await keks.delete_check_rows(db)
    print(f"KEK check rows deleted: {forgotten}", file=out)
    async with engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connections.vacuum_tables(autocommit)
        await keks.vacuum_table(autocommit)
        await auth.vacuum_wiped_tables(autocommit)
        try:
            await autocommit.execute(text("CHECKPOINT"))
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) != "42501":
                raise
            print(f"CHECKPOINT refused for the owner role; run it now: {CHECKPOINT_STEP}", file=out)
        else:
            print("CHECKPOINT done", file=out)


async def wipe(args: argparse.Namespace, settings: Settings, out: TextIO, err: TextIO) -> int:
    """No app-role check here: this runs as the owner role, on purpose (ADR 0004 section 2)."""
    engine = create_migrations_engine(settings)
    try:
        factory = create_session_factory(engine)
        if args.resend:
            return await resend(settings, factory, out)
        refusal = await _refusal(settings, factory)
        if refusal is not None:
            print(f"refused: {refusal}", file=err)
            return 1
        async with system_session(factory) as db:
            contexts = await auth.operator_contexts(db)
        counts: Counter[str] = Counter()
        kinds: Counter[str] = Counter()
        for ctx in contexts:
            try:
                async with user_session(factory, ctx) as db:
                    providers = await connections.delete_all_in(db, ctx)
                    event = await auth.wipe_user(db, ctx.user_id, providers)
            except Exception as error:
                # Only the class: the message of a database error can hold row values.
                counts["failed"] += 1
                kinds[type(error).__name__] += 1
                continue
            if event is None:
                counts["nothing to wipe"] += 1
                continue
            counts["wiped"] += 1
            counts["connections deleted"] += len(event.providers)
            counts["TOTP reset"] += event.totp_reset
            try:
                sent = await _send(settings, factory, event)
            except Exception:
                sent = False
            counts["mails sent" if sent else "mails unsent"] += 1
        _print_counts("wipe:", counts, out)
        if counts["failed"]:
            causes = ", ".join(f"{kind}: {n}" for kind, n in sorted(kinds.items()))
            print(
                f"{counts['failed']} accounts were not processed ({causes}); run it again",
                file=err,
            )
            return 1
        await _finish(engine, out)
        if counts["mails unsent"]:
            print("unsent mails: retry with keys wipe --resend", file=out)
        return 0
    finally:
        await engine.dispose()


async def run(args: argparse.Namespace, settings: Settings, out: TextIO, err: TextIO) -> int:
    if args.action == "rewrap":
        return await rewrap(args, settings, out, err)
    return await wipe(args, settings, out, err)
