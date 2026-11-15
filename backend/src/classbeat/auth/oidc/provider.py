"""The provider's configuration: the one client, its secret's hash, the pairwise key and the
signing keys (ADR 0034 section 1), read once at start.

Settings name the client (``CLASSBEAT_OIDC_PLAYER_CLIENT_ID``) and its exact redirect address
(``CLASSBEAT_OIDC_PLAYER_REDIRECT_URI``); with neither, the provider is off and every ``/oidc``
route answers 404. With both, three secret files must be present and well formed, or web refuses
to start (``OidcConfigError``, whose message names the file, never its content):

- ``oidc_client_secret_hash``: the SHA-256 of the client secret as the client sends it (43
  base64url characters, 32 random bytes), as 64 hex digits;
- ``oidc_pairwise_key``: 32 random bytes as 64 hex digits (it never rotates);
- ``oidc_signing_keys``: the key set (``keyset_policy``), ES256 P-256 private keys.

A new key set takes effect at the next start (the runbook restarts web).
"""

import hmac
import re
from dataclasses import dataclass
from pathlib import Path

from classbeat.auth.keys import token_hash
from classbeat.auth.oidc.keyset_policy import KeysetRefused, parse
from classbeat.auth.oidc.request_policy import Client
from classbeat.auth.oidc.signing import Signer, SignerRefused
from classbeat.config import Settings, secrets_dir

SECRET_HASH_FILE = "oidc_client_secret_hash"  # noqa: S105 (a file name)
PAIRWISE_KEY_FILE = "oidc_pairwise_key"
SIGNING_KEYS_FILE = "oidc_signing_keys"
_HEX_KEY = re.compile(rb"[0-9a-f]{64}\n?")
_CLIENT_SECRET = re.compile(r"[A-Za-z0-9_-]{43}")
_SIGNING_KEYS_MAX = 64 * 1024


class OidcConfigError(RuntimeError):
    """Web refuses to start: the provider is configured but a secret file is missing or bad."""


@dataclass(frozen=True, slots=True)
class Provider:
    issuer: str
    client: Client
    secret_hash: bytes
    pairwise_key: bytes
    signer: Signer

    def __repr__(self) -> str:  # never a key or a hash
        return f"Provider(issuer={self.issuer!r}, client_id={self.client.client_id!r})"

    def secret_matches(self, secret: str) -> bool:
        """Constant time over the hashes; a secret of the wrong shape never matches."""
        if not _CLIENT_SECRET.fullmatch(secret):
            return False
        return hmac.compare_digest(token_hash(secret), self.secret_hash)


def _hex_file(directory: Path, name: str) -> bytes:
    try:
        raw = (directory / name).read_bytes()
    except OSError:
        raise OidcConfigError(f"the secret file {name} is missing or unreadable") from None
    if _HEX_KEY.fullmatch(raw) is None:
        raise OidcConfigError(f"the secret file {name} must hold 64 lowercase hex digits")
    return bytes.fromhex(raw[:64].decode("ascii"))


def _signer(directory: Path) -> Signer:
    try:
        raw = (directory / SIGNING_KEYS_FILE).read_bytes()
    except OSError:
        raise OidcConfigError(f"the secret file {SIGNING_KEYS_FILE} is missing") from None
    if len(raw) > _SIGNING_KEYS_MAX:
        raise OidcConfigError(f"the secret file {SIGNING_KEYS_FILE} is too large")
    try:
        return Signer(parse(raw))
    except (KeysetRefused, SignerRefused) as exc:
        raise OidcConfigError(f"the secret file {SIGNING_KEYS_FILE} is refused: {exc}") from None


def load_provider(settings: Settings, directory: Path | None = None) -> Provider | None:
    client_id, redirect_uri = settings.oidc_player_client_id, settings.oidc_player_redirect_uri
    if client_id is None and redirect_uri is None:
        return None
    if client_id is None or redirect_uri is None:
        raise OidcConfigError("set both the player's client id and its redirect address")
    folder = directory or secrets_dir()
    return Provider(
        issuer=settings.public_base_url,
        client=Client(client_id, redirect_uri),
        secret_hash=_hex_file(folder, SECRET_HASH_FILE),
        pairwise_key=_hex_file(folder, PAIRWISE_KEY_FILE),
        signer=_signer(folder),
    )
