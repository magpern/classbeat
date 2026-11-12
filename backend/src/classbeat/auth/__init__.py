"""Accounts, sessions and sign-in (ADR 0003).

``classbeat.auth`` is the web issuer of ``UserContext`` and the only package that touches the
account tables (``classbeat.auth.tables``; guard 8 in ``tests/test_auth_tables_guard.py``). The
worker's purge task and the CLI call the functions exported here.

The package is lazy (PEP 562): importing ``classbeat.auth`` or one of its modules loads only
that module (and ``classbeat.auth.lazy``, the export table, which imports nothing else of the
package), and a name below is imported on first use. So the worker, which needs only
``purge``, never loads the web-only sign-in and mail modules (``oauth_client``, ``mail``) that
the outbound-allowlist guard exempts (ADR 0006 section 9; ``tests/test_egress_guard.py``).
"""

from typing import TYPE_CHECKING, Any

from classbeat.auth.lazy import EXPORTS as _EXPORTS
from classbeat.auth.lazy import resolve as _resolve

if TYPE_CHECKING:
    from classbeat.auth.accounts import (
        AccountError,
        InviteRow,
        UserRow,
        create_admin_invite,
        create_invite,
        create_user,
        disable_invite,
        find_user,
        get_invite,
        grant_admin,
        grant_admin_if_listed,
        list_invites,
        reset_mfa,
        revoke_admin,
    )
    from classbeat.auth.admin_accounts import (
        AccountRow,
        delete_account,
        disable_account,
        enable_account,
        find_accounts,
        get_account,
    )
    from classbeat.auth.admin_lookup import (
        AccountState,
        account_emails,
        account_states,
        addresses_with_accounts,
        invite_states,
        unused_tier_invites,
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
        mail_handoff_context,
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
    from classbeat.auth.export import account_export
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
    from classbeat.auth.mailprefs import MailPreferences, get_mail_prefs, mail_prefs_for_email
    from classbeat.auth.notices import SecurityNotifier, register_security_notifier
    from classbeat.auth.operator import (
        WipeEvent,
        mark_wipe_mailed,
        pending_wipe_mails,
        vacuum_wiped_tables,
        wipe_mail,
        wipe_user,
    )
    from classbeat.auth.owners import (
        active_email,
        lock_owner_row,
        owner_active,
        stored_admin,
        stored_admins,
    )
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
        admin_mail_test_buckets,
        connect_buckets,
        export_buckets,
        mfa_buckets,
        mowl_playlists_buckets,
        mowl_qr_buckets,
        password_buckets,
        playlist_covers_buckets,
        redeem_buckets,
        spotify_start_buckets,
        swedebeat_mixes_buckets,
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
    from classbeat.auth.unsubscribe_token import UNSUBSCRIBE_ALL, issue_unsubscribe_token

# A name that is not loaded yet is resolved on first access by ``classbeat.auth.lazy.resolve``,
# and an exported one is cached in this module's globals so later reads skip ``__getattr__``.
# ``lazy.EXPORTS`` (exported name -> its module in this package) must match the
# ``TYPE_CHECKING`` imports above and ``__all__``: ``tests/test_egress_runtime.py`` checks it.


def __getattr__(name: str) -> Any:
    value = _resolve(__name__, name)
    if name in _EXPORTS:
        globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})


__all__ = [
    "CSRF_COOKIE",
    "DEVICE_COOKIE",
    "SESSION_COOKIE",
    "UNSUBSCRIBE_ALL",
    "AccountError",
    "AccountRow",
    "AccountState",
    "Attempt",
    "Bucket",
    "EmailAddress",
    "InviteRow",
    "IssuedSession",
    "Keys",
    "MailPreferences",
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
    "account_emails",
    "account_export",
    "account_states",
    "active_email",
    "addresses_with_accounts",
    "admin_buckets",
    "admin_mail_test_buckets",
    "attach_state",
    "bucket_ip",
    "clear_session_cookie",
    "client_ip",
    "connect_buckets",
    "create_admin_invite",
    "create_invite",
    "create_user",
    "current_context",
    "current_session",
    "decrypt_totp_secret",
    "delete_account",
    "derive_key",
    "detach_state",
    "disable_account",
    "disable_invite",
    "enable_account",
    "encrypt_totp_secret",
    "export_buckets",
    "find_accounts",
    "find_user",
    "forget_devices",
    "get_account",
    "get_invite",
    "get_mail_prefs",
    "grant_admin",
    "grant_admin_if_listed",
    "hmac_digest",
    "install",
    "invite_states",
    "is_known_device",
    "issue_unsubscribe_token",
    "list_invites",
    "list_sessions",
    "lock_owner_row",
    "mail_handoff_context",
    "mail_prefs_for_email",
    "make_reset_link_sender",
    "mark_wipe_mailed",
    "mfa_buckets",
    "migrate_v1_secrets",
    "mowl_playlists_buckets",
    "mowl_qr_buckets",
    "normalize_password",
    "operator_contexts",
    "owner_active",
    "password_buckets",
    "pending_wipe_mails",
    "playlist_covers_buckets",
    "purge_expired",
    "record_event",
    "redeem_buckets",
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
    "stored_admin",
    "stored_admins",
    "swedebeat_mixes_buckets",
    "token_hash",
    "totp_rows_by_key",
    "unused_tier_invites",
    "vacuum_wiped_tables",
    "wipe_mail",
    "wipe_user",
]
