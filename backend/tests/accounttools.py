"""Planting and scanning for the account export and deletion tests (ADR 0014; #337)."""

import hashlib
import json
from typing import Any
from uuid import UUID

from classbeat.account.coverage import USER_REFERENCES
from songtools import Rider, SongEnv
from test_song_deletion_paths import Planted, planted

AGENT = "GdprTestBrowser/1.0"


class Person:
    """A rider whose account and owned tables hold at least one row everywhere they can."""

    def __init__(self, found: Planted) -> None:
        self.found = found
        self.rider: Rider = found.rider
        self.env: SongEnv = found.env
        self.user_id: UUID = found.owner
        self.third_email = f"third-{self.user_id.hex[:12]}@example.com"  # not the id as text
        self.ip = f"2001:db8:{self.user_id.hex[:4]}::{self.user_id.hex[4:8]}"  # one per person
        self.email: str = self.env.sql(
            "SELECT email FROM users WHERE id = :u", u=self.user_id
        ).scalar_one()

    def sql(self, statement: str, **params: Any) -> Any:
        return self.env.sql(statement, **params)

    def count(self, table: str, column: str = "owner_id") -> int:
        return self.sql(
            f"SELECT count(*) FROM {table} WHERE {column} = :u",  # noqa: S608
            u=self.user_id,
        ).scalar_one()

    def plant_account_rows(self) -> None:
        """What the songs fixture does not create: the sign-in side and the admin-ish links."""
        u = self.user_id
        statements = (
            "INSERT INTO identities (user_id, provider, subject, email_at_link) "
            "VALUES (:u, 'github', :sub, :email)",
            "INSERT INTO known_devices (user_id, device_hash) VALUES (:u, :hash)",
            "INSERT INTO mfa_totp (user_id, secret_ciphertext, nonce, key_id, confirmed_at) "
            "VALUES (:u, 'cipher', 'nonce', 'app-v1', now())",
            "INSERT INTO mfa_recovery_codes (user_id, code_hash) VALUES (:u, :hash)",
            "INSERT INTO auth_tokens (user_id, purpose, token_hash, new_email, email_at_issue, "
            "expires_at) VALUES (:u, 'change_email', :hash, :email, :email, "
            "now() + interval '1 hour')",
            "INSERT INTO oauth_flows (flow_hash, provider, purpose, state_hash, pkce_verifier, "
            "user_id, expires_at) VALUES (:hash, 'github', 'link', 's', 'v', :u, "
            "now() + interval '1 hour')",
            "INSERT INTO auth_events (user_id, kind, ip, detail) "
            "VALUES (:u, 'sign_in', :ip, CAST(:detail AS jsonb))",
            "UPDATE sessions SET ip = :ip, user_agent = :agent, stepup_at = now() "
            "WHERE user_id = :u",
        )
        params = {
            "u": u,
            "sub": f"sub-{u}",
            "email": self.email,
            "hash": f"h-{u}".encode(),
            "ip": self.ip,
            "agent": AGENT,
            "detail": json.dumps({"email": self.email}),
        }
        for statement in statements:
            self.sql(statement, **params)
        class_id = self.found.class_id
        self.sql(
            "INSERT INTO class_messages (owner_id, class_id, seq, role, body) "
            "VALUES (:u, :c, 1, 'user', 'make it harder')",
            u=u,
            c=class_id,
        )
        self.sql(
            "INSERT INTO saved_workouts (owner_id, class_id, program_id, revision_number, "
            "content_hash, name) VALUES (:u, :c, 4242, 1, :hash, 'Tuesday climb')",
            u=u,
            c=class_id,
            hash=bytes(32),
        )
        code_id = self.sql(
            "INSERT INTO redeem_codes (code_hash, label, plan_id, grant_days, max_redemptions, "
            "expires_at) VALUES (:hash, 'gdpr', 'instructor', 30, 5, now() + interval '30 days') "
            "RETURNING id",
            hash=hashlib.sha256(str(u).encode()).digest(),
        ).scalar_one()
        entitlement = self.sql(
            "INSERT INTO entitlements (owner_id, plan_id, source, code_id, ends_at) "
            "VALUES (:u, 'instructor', 'code', :code, now() + interval '30 days') RETURNING id",
            u=u,
            code=code_id,
        ).scalar_one()
        self.sql(
            "INSERT INTO code_redemptions (owner_id, code_id, entitlement_id) "
            "VALUES (:u, :code, :e)",
            u=u,
            code=code_id,
            e=entitlement,
        )
        # Links that only name the user, and stay when the user goes.
        self.sql(
            "INSERT INTO invites (code_hash, used_by, created_by, expires_at, used_at) "
            "VALUES (:hash, :u, :u, now() + interval '1 day', now())",
            hash=f"i-{u}".encode(),
            u=u,
        )
        third = self.sql(
            "INSERT INTO users (email, email_normalized, email_verified_at) "
            "VALUES (:e, :e, now()) RETURNING id",
            e=self.third_email,
        ).scalar_one()
        self.sql(
            "INSERT INTO entitlements (owner_id, features, source, granted_by) "
            "VALUES (:third, ARRAY['song_picking'], 'grant', :u)",
            third=third,
            u=u,
        )
        # What an operator's delete of this account leaves: the id as the target's text, which
        # ADR 0014 section 5 retains as a bare id (``RETAINED``).
        self.sql(
            "INSERT INTO admin_audit (actor_email, action, target_kind, target_id, outcome, "
            "request_id, ip_hash) VALUES ('operator@example.com', 'user.delete', 'user', :id, "
            "'ok', 'req-2', :ip_hash)",
            id=str(u),
            ip_hash=bytes(32),
        )
        self.sql(
            "INSERT INTO admin_audit (actor_email, action, target_user_id, outcome, request_id, "
            "ip_hash) VALUES ('operator@example.com', 'users.get', :u, 'ok', 'req-1', :ip_hash)",
            u=u,
            ip_hash=bytes(32),
        )


async def person(env: SongEnv) -> Person:
    made = Person(await planted(env))
    made.plant_account_rows()
    return made


def references_with_rows(who: Person) -> dict[tuple[str, str], int]:
    """Rows that refer to ``who`` per (table, column) of the cascading references."""
    return {key: who.count(*key) for key, action in USER_REFERENCES.items() if action == "c"}


def hits(who: Person) -> dict[str, int]:
    """Where the user's id, address or IP still appears, over every column of every table of the
    schema (read from the catalog): uuid columns by id; text, varchar, jsonb and inet columns by
    address, IP and the id written as text."""
    found: dict[str, int] = {}
    columns = who.sql(
        "SELECT table_name, column_name, data_type FROM information_schema.columns c "
        "WHERE table_schema = 'public' AND table_name IN (SELECT tablename FROM pg_tables "
        "WHERE schemaname = 'public') AND data_type IN ('uuid', 'text', 'jsonb', "
        "'character varying', 'inet')"
    ).all()
    for table, column, kind in columns:
        if kind == "uuid":
            where, params = f'"{column}" = :u', {"u": who.user_id}
        else:
            where = (
                f'"{column}"::text ILIKE :email OR "{column}"::text LIKE :ip '
                f'OR "{column}"::text ILIKE :uid'  # the id written as text (a log line, a detail)
            )
            params = {
                "email": f"%{who.email}%",
                "ip": f"%{who.ip}%",
                "uid": f"%{who.user_id}%",
            }
        count = who.sql(
            f'SELECT count(*) FROM "{table}" WHERE {where}',  # noqa: S608
            **params,
        ).scalar_one()
        if count:
            found[f"{table}.{column}"] = count
    return found
