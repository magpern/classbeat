"""``python -m classbeat oidc keys``'s steps (ADR 0034 section 1): the key set in, the key set
(or a listing) out. Nothing here reads or writes a file: the CLI reads the set on stdin and
writes the new one on stdout, and the operator moves it into the secret file (runbook
``docs/runbooks/oidc-keys.md``). A listing shows kids, states and times, never a key.

- ``list``: one line per key;
- ``add-next [--kid K]``: a new P-256 key as the next key (the current one on an empty set);
- ``promote``: the next key signs, once it has been published for 24 hours and 5 minutes;
- ``retire``: drop the retired keys that are no longer published.

Every refusal is a ``ValueError`` whose message names the rule.
"""

import secrets
from datetime import datetime

from classbeat.auth.oidc import keyset_policy as policy
from classbeat.auth.oidc.signing import Signer, generate_pem

ACTIONS = ("list", "add-next", "promote", "retire")


def _when(value: datetime | None) -> str:
    return "-" if value is None else value.isoformat(timespec="seconds")


def oidc_keys(action: str, current: str, now: datetime, *, kid: str | None = None) -> str:
    """The output of ``action`` on the key set ``current`` (JSON text; empty for a new set)."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    keys = policy.parse(current or '{"keys": []}', allow_empty=action == "add-next")
    if action == "list":
        shown = {key.kid for key in policy.published(keys, now)}
        return "".join(
            f"{key.kid} {key.state} published_at={_when(key.published_at)} "
            f"retired_at={_when(key.retired_at)} {'published' if key.kid in shown else 'expired'}\n"
            for key in keys
        )
    if action == "add-next":
        keys = policy.add_next(
            keys, kid or f"k{now:%Y%m%d}-{secrets.token_hex(3)}", generate_pem(), now
        )
    elif action == "promote":
        keys = policy.promote(keys, now)
    else:
        keys = policy.drop_expired(keys, now)
    Signer(keys)  # every key must load as a P-256 private key before the set is written
    return policy.dump(keys)
