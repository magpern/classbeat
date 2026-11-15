"""ES256 signing and the published key set (ADR 0034 section 1).

``Signer`` holds the parsed private keys of the key set (``keyset_policy``); it signs only with
the current key and publishes only public parts. ``repr`` shows kids, never key material.
"""

from datetime import datetime
from typing import Any

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import ECKey

from classbeat.auth.oidc.keyset_policy import KeyEntry, published, signing_key

ALGORITHM = "ES256"


class SignerRefused(ValueError):
    """A key in the set is not an ES256 (P-256) private key."""


def _private_key(entry: KeyEntry) -> ECKey:
    try:
        key = ECKey.import_key(entry.pem)
    except (JoseError, ValueError, TypeError):
        raise SignerRefused(f"key {entry.kid} is not a PEM EC key") from None
    if key.curve_name != "P-256" or not key.is_private:
        raise SignerRefused(f"key {entry.kid} must be a P-256 private key")
    return key


class Signer:
    __slots__ = ("_current", "_entries", "_keys")

    def __init__(self, entries: list[KeyEntry]) -> None:
        self._entries = list(entries)
        self._keys = {entry.kid: _private_key(entry) for entry in self._entries}
        self._current = signing_key(self._entries).kid

    def __repr__(self) -> str:
        return f"Signer(current={self._current!r}, kids={sorted(self._keys)!r})"

    @property
    def current_kid(self) -> str:
        return self._current

    def jwks(self, now: datetime) -> dict[str, Any]:
        """The public key set: the current key, the next one and recently retired ones."""
        keys = [
            self._keys[entry.kid].as_dict(private=False, kid=entry.kid, use="sig", alg=ALGORITHM)
            for entry in published(self._entries, now)
        ]
        return {"keys": keys}

    def sign(self, claims: dict[str, Any], *, typ: str = "JWT") -> str:
        """Sign with the current key; ``typ`` is ``JWT`` (an ID token) or ``logout+jwt`` (an
        event to the player, ADR 0034 section 2)."""
        header = {"alg": ALGORITHM, "typ": typ, "kid": self._current}
        return jwt.encode(header, claims, self._keys[self._current], algorithms=[ALGORITHM])


def generate_pem() -> str:
    """A new P-256 private key as PKCS#8 PEM (for ``oidc keys add-next``)."""
    return ECKey.generate_key("P-256", private=True).as_pem(private=True).decode("ascii")
