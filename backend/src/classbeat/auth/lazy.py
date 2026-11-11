"""The lazy loading of ``classbeat.auth`` (PEP 562, ADR 0006 section 9): which module of the
package each exported name lives in, and how a name is resolved on first use. The package's
``__init__`` keeps the ``TYPE_CHECKING`` imports and ``__all__`` that type checkers read;
``tests/test_egress_runtime.py`` checks the three agree. Imports nothing from the package, so
importing ``classbeat.auth`` still loads no other module of it.
"""

from importlib import import_module
from importlib.util import find_spec
from typing import Any

# Exported name -> its module in ``classbeat.auth``.
EXPORTS: dict[str, str] = {
    "AccountError": "accounts",
    "UserRow": "accounts",
    "create_invite": "accounts",
    "InviteRow": "accounts",
    "create_admin_invite": "accounts",
    "disable_invite": "accounts",
    "get_invite": "accounts",
    "list_invites": "accounts",
    "create_user": "accounts",
    "find_user": "accounts",
    "grant_admin": "accounts",
    "grant_admin_if_listed": "accounts",
    "reset_mfa": "accounts",
    "revoke_admin": "accounts",
    "AccountRow": "admin_accounts",
    "get_account": "admin_accounts",
    "delete_account": "admin_accounts",
    "disable_account": "admin_accounts",
    "enable_account": "admin_accounts",
    "find_accounts": "admin_accounts",
    "AccountState": "admin_lookup",
    "account_emails": "admin_lookup",
    "account_states": "admin_lookup",
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
    "mail_handoff_context": "deps",
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
    "MailPreferences": "mailprefs",
    "get_mail_prefs": "mailprefs",
    "mail_prefs_for_email": "mailprefs",
    "UNSUBSCRIBE_ALL": "unsubscribe_token",
    "issue_unsubscribe_token": "unsubscribe_token",
    "Keys": "keys",
    "decrypt_totp_secret": "keys",
    "derive_key": "keys",
    "encrypt_totp_secret": "keys",
    "hmac_digest": "keys",
    "token_hash": "keys",
    "SecurityNotifier": "notices",
    "register_security_notifier": "notices",
    "account_export": "export",
    "export_buckets": "ratelimit",
    "mowl_playlists_buckets": "ratelimit",
    "mowl_qr_buckets": "ratelimit",
    "playlist_covers_buckets": "ratelimit",
    "swedebeat_mixes_buckets": "ratelimit",
    "owner_active": "owners",
    "active_email": "owners",
    "stored_admin": "owners",
    "lock_owner_row": "owners",
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
    "redeem_buckets": "ratelimit",
    "spotify_start_buckets": "ratelimit",
    "admin_buckets": "ratelimit",
    "admin_mail_test_buckets": "ratelimit",
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


def resolve(package: str, name: str) -> Any:
    """``name`` of ``package``: an exported name from its module, or a submodule (as
    ``auth.tables``); ``AttributeError`` for anything else, a dotted or private name included."""
    module = EXPORTS.get(name)
    if module is not None:
        return getattr(import_module(f"{package}.{module}"), name)
    if not name.startswith("_") and "." not in name and _is_submodule(package, name):
        return import_module(f"{package}.{name}")
    raise AttributeError(f"module {package!r} has no attribute {name!r}")


def _is_submodule(package: str, name: str) -> bool:
    try:
        return find_spec(f"{package}.{name}") is not None
    except (
        ImportError
    ):  # not a package below ``name``: an attribute lookup answers "no", never raises
        return False
