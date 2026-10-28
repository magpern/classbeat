"""Tables that are not per-user (ADR 0002 section 3)."""

from types import MappingProxyType

# table name -> one-line reason it has no ``owner_id`` and no row-level security.
# Every other table in ``public`` is owned; the guard test in ``tests/test_rls_guard.py``
# holds both sides to the contract. The M1 account tables (ADR 0003 section 1) are reached only
# through ``classbeat.auth`` (guard 8); shared music analysis arrives with M2.
# ``connections`` (ADR 0004) is owned and therefore not listed.
SYSTEM_TABLES: MappingProxyType[str, str] = MappingProxyType(
    {
        "alembic_version": "Alembic migration bookkeeping; no app access",
        "users": "accounts, read at sign-in before any user is known; owned tables reference it",
        "identities": "linked sign-in providers, looked up by (provider, subject) at sign-in",
        "sessions": "server-side sessions, looked up by token hash before any user is known",
        "known_devices": "device tokens (HMAC only) that exempt a user from the per-account cap",
        "pending_signups": "sign-ups waiting for a mail link; no user exists yet",
        "auth_tokens": "single-use reset and email-change tokens (hash only), looked up by hash",
        "oauth_flows": "in-flight OAuth flows, taken by flow hash before a user is known",
        "invites": "invite codes (hash only), checked before a user exists",
        "mfa_totp": "admin and step-up TOTP secrets (AES-GCM), read during sign-in",
        "mfa_recovery_codes": "TOTP recovery codes (HMAC only), read during sign-in",
        "rate_limits": "counters keyed by an HMAC of the bucket, before any user is known",
        "auth_events": "security event log; keeps a null user_id after the user is deleted",
        "connection_keks": "check values of the KEK ids (ADR 0004), read at startup before "
        "any user; reached only through classbeat.crypto.keks",
    }
)
# System tables the app role must have no privilege on at all (the rest get DML).
NO_APP_ACCESS = frozenset({"alembic_version"})
