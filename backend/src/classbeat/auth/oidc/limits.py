"""The provider's rate limits (ADR 0034 section 1; ADR 0003 section 9, reserve first). Pure:
buckets only.

- authorize starts: 120 per address and hour at ``/oidc/authorize`` (every request counts), and
  30 per user and hour where the user is first known, at the continue step (every request
  counts);
- consent posts: 20 per user and hour (every request counts);
- the token endpoint: everyone together 600 per minute (every call counts), then 20 **failures**
  per address per 10 minutes (reserved first, released by a success: the player's one server
  makes every call, so successes are not counted per address);
- failures per client are counted only for an admin warning above 100 per hour; that count never
  refuses a call, so nobody can lock out the one client.
"""

from classbeat.auth.clientip import bucket_ip
from classbeat.auth.ratelimit import Bucket, Limit

AUTHORIZE_IP = Limit("oidc-authorize", "ip", 120, 3600)
AUTHORIZE_USER = Limit("oidc-authorize", "account", 30, 3600)
CONSENT_USER = Limit("oidc-consent", "account", 20, 3600)
TOKEN_ALL = Limit("oidc-token", "global", 600, 60)
TOKEN_FAIL_IP = Limit("oidc-token-fail", "ip", 20, 10 * 60)
CLIENT_FAIL_WARN = Limit("oidc-client-fail", "client", 100, 3600)
GLOBAL_VALUE = "all"


def authorize_ip_buckets(ip: str) -> list[Bucket]:
    return [Bucket(AUTHORIZE_IP, bucket_ip(ip))]


def authorize_user_buckets(user_id: str) -> list[Bucket]:
    return [Bucket(AUTHORIZE_USER, user_id)]


def consent_buckets(user_id: str) -> list[Bucket]:
    return [Bucket(CONSENT_USER, user_id)]


def token_global_buckets() -> list[Bucket]:
    return [Bucket(TOKEN_ALL, GLOBAL_VALUE)]


def token_failure_buckets(ip: str) -> list[Bucket]:
    return [Bucket(TOKEN_FAIL_IP, bucket_ip(ip))]


def client_warning_buckets(client_id: str) -> list[Bucket]:
    return [Bucket(CLIENT_FAIL_WARN, client_id)]
