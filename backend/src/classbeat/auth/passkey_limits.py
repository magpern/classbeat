"""The passkey rate limits of ADR 0019 section 10. Pure: buckets only.

Register, rename and remove (W2, #772) count every request (a refused one too; nothing is
released), like the export and redeem limits in ``ratelimit``. Register start and finish share
their buckets, so ten per user and hour is five complete registrations.

Sign-in (W3, #773):

- **Start** counts every request in up to three buckets, reserved **in this order and stopping at
  the first refusal** (``start_buckets``): the address (``bucket_ip``: IPv4 as it is, IPv6 per
  /64), then for IPv6 its /48, then everyone together. ``RateLimiter.begin`` takes a slot in every
  bucket it is given even after a refusal, so the caller reserves them one at a time: a start
  refused by a narrower bucket takes no slot in a wider one, and the global count over a window
  is at most 60 per IPv4 address and 300 per IPv6 /48. The global bucket answers 503
  ``passkey_busy`` (``StartBucket.busy``), the others 429.
- **Failures** count like the password sign-in's: per address always, per account (the
  credential's user) unless from a known device; a success releases them.
"""

from dataclasses import dataclass

from classbeat.auth.clientip import bucket_ip, bucket_ip48
from classbeat.auth.ratelimit import Bucket, Limit

PASSKEY_REGISTER_USER = Limit("passkey-register", "account", 10, 3600)
PASSKEY_REGISTER_IP = Limit("passkey-register", "ip", 30, 3600)
PASSKEY_MANAGE_USER = Limit("passkey-manage", "account", 30, 3600)

START_WINDOW = 5 * 60  # as long as a challenge row lives (section 10)
PASSKEY_SIGNIN_START_IP = Limit("passkey-signin-start", "ip", 60, START_WINDOW)
PASSKEY_SIGNIN_START_IP48 = Limit("passkey-signin-start", "ip48", 300, START_WINDOW)
PASSKEY_SIGNIN_START_ALL = Limit("passkey-signin-start", "global", 20_000, START_WINDOW)
GLOBAL_VALUE = "all"  # the one value of the global bucket
PASSKEY_SIGNIN_FAIL_ACCOUNT = Limit("passkey-signin", "account", 20, 3600)
PASSKEY_SIGNIN_FAIL_IP = Limit("passkey-signin", "ip", 30, 15 * 60)


@dataclass(frozen=True, slots=True)
class StartBucket:
    """One sign-in start bucket; ``busy`` marks the global one (503, not 429)."""

    bucket: Bucket
    busy: bool = False


def register_buckets(user_id: str, ip: str) -> list[Bucket]:
    """A register start or finish: per user and per client address."""
    return [Bucket(PASSKEY_REGISTER_USER, user_id), Bucket(PASSKEY_REGISTER_IP, bucket_ip(ip))]


def manage_buckets(user_id: str) -> list[Bucket]:
    """A rename or a remove: per user."""
    return [Bucket(PASSKEY_MANAGE_USER, user_id)]


def start_buckets(ip: str) -> list[StartBucket]:
    """A sign-in start's buckets, narrowest first: the address, the IPv6 /48 (none for IPv4),
    everyone. The caller reserves them in this order and stops at the first refusal."""
    found = [StartBucket(Bucket(PASSKEY_SIGNIN_START_IP, bucket_ip(ip)))]
    wide = bucket_ip48(ip)
    if wide is not None:
        found.append(StartBucket(Bucket(PASSKEY_SIGNIN_START_IP48, wide)))
    found.append(StartBucket(Bucket(PASSKEY_SIGNIN_START_ALL, GLOBAL_VALUE), busy=True))
    return found


def failure_ip_buckets(ip: str) -> list[Bucket]:
    """A sign-in finish, before anything is known: per address; released on success."""
    return [Bucket(PASSKEY_SIGNIN_FAIL_IP, bucket_ip(ip))]


def failure_account_buckets(user_id: str, *, known_device: bool) -> list[Bucket]:
    """A sign-in finish once the credential names its user: per account, except from one of the
    user's known devices (ADR 0003 section 9); released on success."""
    return [] if known_device else [Bucket(PASSKEY_SIGNIN_FAIL_ACCOUNT, user_id)]
