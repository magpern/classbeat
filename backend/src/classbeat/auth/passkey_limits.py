"""The passkey rate limits of ADR 0019 section 10 that W2 (#772) uses. Pure: buckets only.

Every request counts (a refused one too; nothing is released), like the export and redeem limits
in ``ratelimit``. Register start and finish share their buckets, so ten per user and hour is five
complete registrations. The sign-in buckets come with the sign-in routes (W3).
"""

from classbeat.auth.clientip import bucket_ip
from classbeat.auth.ratelimit import Bucket, Limit

PASSKEY_REGISTER_USER = Limit("passkey-register", "account", 10, 3600)
PASSKEY_REGISTER_IP = Limit("passkey-register", "ip", 30, 3600)
PASSKEY_MANAGE_USER = Limit("passkey-manage", "account", 30, 3600)


def register_buckets(user_id: str, ip: str) -> list[Bucket]:
    """A register start or finish: per user and per client address."""
    return [Bucket(PASSKEY_REGISTER_USER, user_id), Bucket(PASSKEY_REGISTER_IP, bucket_ip(ip))]


def manage_buckets(user_id: str) -> list[Bucket]:
    """A rename or a remove: per user."""
    return [Bucket(PASSKEY_MANAGE_USER, user_id)]
