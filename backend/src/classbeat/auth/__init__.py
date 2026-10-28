"""Accounts, sessions and sign-in (ADR 0003).

``classbeat.auth`` is the web issuer of ``UserContext`` and the only package that touches the
account tables (``classbeat.auth.tables``; guard 8 in ``tests/test_auth_tables_guard.py``). The
worker's purge task and the CLI call the functions exported here.
"""

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
    mfa_buckets,
    password_buckets,
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

__all__ = [
    "CSRF_COOKIE",
    "DEVICE_COOKIE",
    "SESSION_COOKIE",
    "AccountError",
    "Attempt",
    "Bucket",
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
    "UserRow",
    "attach_state",
    "bucket_ip",
    "clear_session_cookie",
    "client_ip",
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
    "mfa_buckets",
    "normalize_password",
    "password_buckets",
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
    "rotate_session",
    "send_reset_link",
    "set_device_cookie",
    "set_session_cookie",
    "start_session",
    "token_hash",
]
