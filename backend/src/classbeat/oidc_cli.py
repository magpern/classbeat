"""``python -m classbeat oidc keys ...`` and ``admin player-subject <email>`` (ADR 0034 section 1).

``oidc keys list|add-next|promote|retire`` reads the key set (the ``oidc_signing_keys`` secret
file's JSON) on stdin and writes the new set on stdout; ``list`` prints kids, states and times
only. A step that writes private keys refuses when stdout is a terminal, so a key never lands
on a screen or in a terminal's scroll-back: redirect it into a new file and move that into the
secret file (``docs/runbooks/oidc-keys.md``). It needs no database.

``admin player-subject <email>`` runs in the ``maint`` compose service as the owner role
(``docker compose --profile maint run --rm maint python -m classbeat admin player-subject
<email>``): it prints the user's pairwise subject for the player's operator (player ADR 0010
section 4) and writes one ``admin_audit`` row (``player.subject``, ``outcome = 'ok'``) in the
same transaction. It changes nothing else.
"""

import argparse
import hashlib
import sys
import uuid
from datetime import UTC, datetime
from typing import Any, TextIO

from sqlalchemy import text

from classbeat import auth
from classbeat.config import Settings
from classbeat.db import create_migrations_engine, create_session_factory, system_session

AUDIT_ACTION = "player.subject"
_AUDIT = text(
    "INSERT INTO admin_audit (actor_email, action, target_user_id, target_kind, target_id, "
    "params, outcome, request_id, ip_hash) VALUES ('cli', :action, :user_id, 'user', :target, "
    "'{}'::jsonb, 'ok', :request_id, :ip_hash)"
)
_CLI_IP_HASH = hashlib.sha256(b"cli").digest()


def add_oidc_subcommands(sub: Any) -> None:
    """``oidc keys <action>`` (``sub``: the top-level subparsers)."""
    oidc = sub.add_parser("oidc", help="the player sign-in provider (ADR 0034)").add_subparsers(
        dest="action", required=True
    )
    keys = oidc.add_parser("keys", help="rotate the signing keys: set on stdin, new set on stdout")
    keys.add_argument("step", choices=("list", "add-next", "promote", "retire"))
    keys.add_argument("--kid", help="add-next: the new key's id (default: dated and random)")


def add_player_subject(admin: Any) -> None:
    """``admin player-subject <email>`` (``admin``: the ``admin`` subparsers)."""
    admin.add_parser(
        "player-subject", help="print the user's player subject (maint service; audited)"
    ).add_argument("email")


def run_keys(
    args: argparse.Namespace, out: TextIO, err: TextIO, stdin: TextIO | None = None
) -> int:
    if args.step != "list" and out.isatty():
        print("error: refusing to write private keys to a terminal; redirect stdout", file=err)
        return 1
    try:
        result = auth.oidc_keys(
            args.step, (stdin or sys.stdin).read(), datetime.now(UTC), kid=args.kid
        )
    except ValueError as exc:
        print(f"error: {exc}", file=err)
        return 1
    out.write(result)
    return 0


async def run_player_subject(
    args: argparse.Namespace, settings: Settings, out: TextIO, err: TextIO
) -> int:
    engine = create_migrations_engine(settings)
    try:
        async with system_session(create_session_factory(engine)) as db:
            found = await auth.player_subject(db, settings, args.email)
            await db.execute(
                _AUDIT,
                {
                    "action": AUDIT_ACTION,
                    "user_id": found.user_id,
                    "target": str(found.user_id),
                    "request_id": f"cli-{uuid.uuid4()}",
                    "ip_hash": _CLI_IP_HASH,
                },
            )
        print(found.subject, file=out)
        return 0
    except auth.AccountError as exc:
        print(f"error: {exc}", file=err)
        return 1
    finally:
        await engine.dispose()
