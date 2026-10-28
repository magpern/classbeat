"""Operator subcommands of ``python -m classbeat`` (ADR 0003 section 7).

``keys rewrap`` and ``keys wipe`` (ADR 0004) live in ``classbeat.keys_cli``.

``invite create``, ``admin grant``, ``admin revoke`` and ``admin mfa-reset`` run as the app role
through the functions of ``classbeat.auth``; they never touch the tables. In the stack:
``docker compose exec web python -m classbeat admin grant owner@example.com``. With no
subcommand ``python -m classbeat`` still starts the server (``--host``, ``--port``).
"""

import argparse
import asyncio
import re
import sys
from datetime import timedelta
from typing import TextIO

from classbeat import auth
from classbeat.config import Settings
from classbeat.db import (
    create_engine,
    create_session_factory,
    event_loop_factory,
    system_session,
    verify_database_role,
)
from classbeat.keys_cli import add_keys_subcommands
from classbeat.keys_cli import run as run_keys
from classbeat.mail import Mailer, MailError

_DURATION = re.compile(r"(\d+)([dhm])")
_UNITS = {"d": "days", "h": "hours", "m": "minutes"}
MAX_INVITE_DAYS = 365


def parse_duration(value: str) -> timedelta:
    """``7d``, ``12h`` or ``30m``; at least one minute, at most a year."""
    match = _DURATION.fullmatch(value.strip())
    if match is None:
        raise argparse.ArgumentTypeError("use a number and d, h or m, for example 7d")
    duration = timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})
    if not timedelta(minutes=1) <= duration <= timedelta(days=MAX_INVITE_DAYS):
        raise argparse.ArgumentTypeError("the duration must be between 1 minute and 365 days")
    return duration


def add_subcommands(parser: argparse.ArgumentParser) -> None:
    """Optional subparsers: without one the server starts."""
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    invite = sub.add_parser("invite", help="manage invite codes").add_subparsers(
        dest="action", required=True
    )
    create = invite.add_parser("create", help="print one new invite code (shown once)")
    create.add_argument("--expires", type=parse_duration, default=timedelta(days=7), metavar="7d")
    admin = sub.add_parser("admin", help="manage admins").add_subparsers(
        dest="action", required=True
    )
    for name, text in (
        ("grant", "make an existing user an admin"),
        ("revoke", "make an admin a plain user"),
        ("mfa-reset", "remove the user's TOTP and recovery codes and mail a reset link"),
    ):
        admin.add_parser(name, help=text).add_argument("email")
    add_keys_subcommands(sub)


async def _admin(args: argparse.Namespace, settings: Settings, out: TextIO, err: TextIO) -> int:
    engine = create_engine(settings)
    try:
        await verify_database_role(engine)
        factory = create_session_factory(engine)
        async with system_session(factory) as db:
            if args.command == "invite":
                print(await auth.create_invite(db, args.expires), file=out)  # shown once
                print("invite code created; it is not stored and cannot be shown again", file=err)
                return 0
            if args.action == "grant":
                changed = await auth.grant_admin(db, args.email)
                print(
                    "admin granted; all sessions revoked" if changed else "already an admin",
                    file=err,
                )
                return 0
            if args.action == "revoke":
                changed = await auth.revoke_admin(db, settings, args.email)
                print(
                    "admin revoked; all sessions revoked" if changed else "not an admin", file=err
                )
                return 0
            user = await auth.reset_mfa(db, args.email)
        auth.register_reset_link_sender(
            auth.make_reset_link_sender(settings, factory, Mailer(settings))
        )
        try:
            sent = await auth.send_reset_link(user.id, user.email)
        except MailError as exc:
            print(
                f"TOTP and recovery codes removed; sessions revoked; reset link NOT mailed: {exc}",
                file=err,
            )
            return 1
        print(
            "TOTP and recovery codes removed; all sessions revoked; "
            + ("reset link mailed" if sent else "reset link NOT mailed: no sender is registered"),
            file=err,
        )
        return 0
    except auth.AccountError as exc:
        print(f"error: {exc}", file=err)
        return 1
    finally:
        await engine.dispose()


def run_command(
    args: argparse.Namespace,
    settings: Settings,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run an operator subcommand; the exit code is 0, or 1 for a refusal."""
    command = run_keys if args.command == "keys" else _admin
    return asyncio.run(
        command(args, settings, out or sys.stdout, err or sys.stderr),
        loop_factory=event_loop_factory(),
    )
