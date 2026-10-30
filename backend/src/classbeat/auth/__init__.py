"""Accounts, sessions and sign-in (ADR 0003).

``classbeat.auth`` is the web issuer of ``UserContext`` and the only package that touches the
account tables (``classbeat.auth.tables``; guard 8 in ``tests/test_auth_tables_guard.py``). The
worker's purge task and the CLI call the functions exported here.

The package is lazy (PEP 562): importing ``classbeat.auth`` or one of its modules loads only
that module, and a name below is imported on first use. So the worker, which needs only
``purge``, never loads the web-only sign-in and mail modules (``oauth_client``, ``mail``) that
the outbound-allowlist guard exempts (ADR 0006 section 9; ``tests/test_egress_guard.py``).
"""

from importlib import import_module
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from classbeat.auth.accounts import (
        AccountError,
        UserRow,
        create_invite,
        create_user,
        find_user,
        grant_admin,
        grant_admin_if_listed,
        reset_mfa,
        revoke_admin,
    )
    from classbeat.auth.clientip import bucket_ip, client_ip
    from classbeat.auth.cookies import (
        CSRF_COOKIE,
        DEVICE_COOKIE,
        SESSION_COOKIE,
        clear_session_cookie,
        set_device_cookie,
        set_session_cookie,
    )
    from classbeat.auth.deps import (
        current_context,
        current_session,
        operator_contexts,
        request_device_token,
        request_ip,
        request_session_token,
        require_admin,
        require_recent_auth,
        require_stepup,
        require_user,
    )
    from classbeat.auth.devices import forget_devices, is_known_device, remember_device
    from classbeat.auth.events import record_event
    from classbeat.auth.flowtools import EmailAddress
    from classbeat.auth.hooks import register_reset_link_sender, send_reset_link
    from classbeat.auth.keys import (
        Keys,
        decrypt_totp_secret,
        derive_key,
        encrypt_totp_secret,
        hmac_digest,
        token_hash,
    )
    from classbeat.auth.notices import SecurityNotifier, register_security_notifier
    from classbeat.auth.operator import (
        WipeEvent,
        mark_wipe_mailed,
        pending_wipe_mails,
        vacuum_wiped_tables,
        wipe_mail,
        wipe_user,
    )
    from classbeat.auth.owners import owner_active
    from classbeat.auth.passwords import (
        PasswordHashBusy,
        PasswordHasher,
        PasswordRuleError,
        normalize_password,
    )
    from classbeat.auth.purge import purge_expired
    from classbeat.auth.ratelimit import (
        Attempt,
        Bucket,
        RateLimited,
        RateLimiter,
        admin_buckets,
        connect_buckets,
        mfa_buckets,
        password_buckets,
        spotify_start_buckets,
    )
    from classbeat.auth.reset_routes import make_reset_link_sender
    from classbeat.auth.sessions import (
        IssuedSession,
        Resolution,
        SessionGone,
        SessionInfo,
        list_sessions,
        resolve_session,
        revoke_session,
        revoke_user_sessions,
        rotate_session,
        start_session,
    )
    from classbeat.auth.setup import attach_state, detach_state, install
    from classbeat.auth.totp_keys import (
        TotpUnreadable,
        migrate_v1_secrets,
        rewrap_v2_secrets,
        totp_rows_by_key,
    )

# Exported name -> its module in this package. ``tests/test_egress_runtime.py`` checks it matches
# the ``TYPE_CHECKING`` imports above and ``__all__``.
_EXPORTS: dict[str, str] = {
    "AccountError": "accounts",
    "UserRow": "accounts",
    "create_invite": "accounts",
    "create_user": "accounts",
    "find_user": "accounts",
    "grant_admin": "accounts",
    "grant_admin_if_listed": "accounts",
    "reset_mfa": "accounts",
    "revoke_admin": "accounts",
    "bucket_ip": "clientip",
    "client_ip": "clientip",
    "CSRF_COOKIE": "cookies",
    "DEVICE_COOKIE": "cookies",
    "SESSION_COOKIE": "cookies",
    "clear_session_cookie": "cookies",
    "set_device_cookie": "cookies",
    "set_session_cookie": "cookies",
    "current_context": "deps",
    "current_session": "deps",
    "operator_contexts": "deps",
    "request_device_token": "deps",
    "request_ip": "deps",
    "request_session_token": "deps",
    "require_admin": "deps",
    "require_recent_auth": "deps",
    "require_stepup": "deps",
    "require_user": "deps",
    "forget_devices": "devices",
    "is_known_device": "devices",
    "remember_device": "devices",
    "record_event": "events",
    "EmailAddress": "flowtools",
    "register_reset_link_sender": "hooks",
    "send_reset_link": "hooks",
    "Keys": "keys",
    "decrypt_totp_secret": "keys",
    "derive_key": "keys",
    "encrypt_totp_secret": "keys",
    "hmac_digest": "keys",
    "token_hash": "keys",
    "SecurityNotifier": "notices",
    "register_security_notifier": "notices",
    "owner_active": "owners",
    "WipeEvent": "operator",
    "mark_wipe_mailed": "operator",
    "pending_wipe_mails": "operator",
    "vacuum_wiped_tables": "operator",
    "wipe_mail": "operator",
    "wipe_user": "operator",
    "PasswordHashBusy": "passwords",
    "PasswordHasher": "passwords",
    "PasswordRuleError": "passwords",
    "normalize_password": "passwords",
    "purge_expired": "purge",
    "Attempt": "ratelimit",
    "Bucket": "ratelimit",
    "RateLimited": "ratelimit",
    "RateLimiter": "ratelimit",
    "connect_buckets": "ratelimit",
    "mfa_buckets": "ratelimit",
    "password_buckets": "ratelimit",
    "spotify_start_buckets": "ratelimit",
    "admin_buckets": "ratelimit",
    "make_reset_link_sender": "reset_routes",
    "IssuedSession": "sessions",
    "Resolution": "sessions",
    "SessionGone": "sessions",
    "SessionInfo": "sessions",
    "list_sessions": "sessions",
    "resolve_session": "sessions",
    "revoke_session": "sessions",
    "revoke_user_sessions": "sessions",
    "rotate_session": "sessions",
    "start_session": "sessions",
    "attach_state": "setup",
    "detach_state": "setup",
    "install": "setup",
    "TotpUnreadable": "totp_keys",
    "migrate_v1_secrets": "totp_keys",
    "rewrap_v2_secrets": "totp_keys",
    "totp_rows_by_key": "totp_keys",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is not None:
        value = getattr(import_module(f"{__name__}.{module}"), name)
        globals()[name] = value
        return value
    if not name.startswith("_") and "." not in name and _is_submodule(name):
        return import_module(f"{__name__}.{name}")  # a submodule, as ``auth.tables``
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _is_submodule(name: str) -> bool:
    try:
        return find_spec(f"{__name__}.{name}") is not None
    except (
        ImportError
    ):  # not a package below ``name``: an attribute lookup answers "no", never raises
        return False


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})


__all__ = [
    "CSRF_COOKIE",
    "DEVICE_COOKIE",
    "SESSION_COOKIE",
    "AccountError",
    "Attempt",
    "Bucket",
    "EmailAddress",
    "IssuedSession",
    "Keys",
    "PasswordHashBusy",
    "PasswordHasher",
    "PasswordRuleError",
    "RateLimited",
    "RateLimiter",
    "Resolution",
    "SecurityNotifier",
    "SessionGone",
    "SessionInfo",
    "TotpUnreadable",
    "UserRow",
    "WipeEvent",
    "admin_buckets",
    "attach_state",
    "bucket_ip",
    "clear_session_cookie",
    "client_ip",
    "connect_buckets",
    "create_invite",
    "create_user",
    "current_context",
    "current_session",
    "decrypt_totp_secret",
    "derive_key",
    "detach_state",
    "encrypt_totp_secret",
    "find_user",
    "forget_devices",
    "grant_admin",
    "grant_admin_if_listed",
    "hmac_digest",
    "install",
    "is_known_device",
    "list_sessions",
    "make_reset_link_sender",
    "mark_wipe_mailed",
    "mfa_buckets",
    "migrate_v1_secrets",
    "normalize_password",
    "operator_contexts",
    "owner_active",
    "password_buckets",
    "pending_wipe_mails",
    "purge_expired",
    "record_event",
    "register_reset_link_sender",
    "register_security_notifier",
    "remember_device",
    "request_device_token",
    "request_ip",
    "request_session_token",
    "require_admin",
    "require_recent_auth",
    "require_stepup",
    "require_user",
    "reset_mfa",
    "resolve_session",
    "revoke_admin",
    "revoke_session",
    "revoke_user_sessions",
    "rewrap_v2_secrets",
    "rotate_session",
    "send_reset_link",
    "set_device_cookie",
    "set_session_cookie",
    "spotify_start_buckets",
    "start_session",
    "token_hash",
    "totp_rows_by_key",
    "vacuum_wiped_tables",
    "wipe_mail",
    "wipe_user",
]
