"""Pure pieces of the connect flow (ADR 0009 sections 1 to 4): the Client ID check, the PKCE
verifier and challenge, the state, their hashes and the authorize URL. No I/O."""

import base64
import hashlib
import re
import secrets
from typing import Final
from urllib.parse import urlencode

from classbeat.spotify.http import AUTHORIZE_URL

# Section 1: exactly these are asked for, never ``user-read-private`` or ``user-read-email``.
# ``playlist-modify-public`` since roadmap Amendment 27 (#1155): Spotify may make a playlist
# classbeat creates public, and writing its items then needs it. A grant needs only
# ``REQUIRED_SCOPES`` (the original two), so a connection made before still connects and works
# for a private playlist.
REQUIRED_SCOPES: Final = ("playlist-read-private", "playlist-modify-private")
SCOPES: Final = (*REQUIRED_SCOPES, "playlist-modify-public")
SCOPE: Final = " ".join(SCOPES)
CLIENT_ID: Final = re.compile(r"[0-9a-fA-F]{32}")
CLIENT_ID_INPUT_MAX: Final = 256
STATE_BYTES: Final = 32
VERIFIER_BYTES: Final = 32  # 43 characters (section 3)
STATE_MAX: Final = 128  # a longer callback ``state`` is no state of ours


def normalize_client_id(value: str) -> str | None:
    """Trimmed, ``^[0-9a-fA-F]{32}$``, lower case; else ``None`` (422 ``invalid_client_id``)."""
    trimmed = value.strip()
    if CLIENT_ID.fullmatch(trimmed) is None:
        return None
    return trimmed.lower()


def new_state() -> str:
    return secrets.token_urlsafe(STATE_BYTES)


def new_verifier() -> str:
    return secrets.token_urlsafe(VERIFIER_BYTES)


def challenge_of(verifier: str) -> str:
    """S256 (RFC 7636)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def session_hash(session_id: object) -> bytes:
    """SHA-256 of the session id that started the flow (section 3)."""
    return sha256(str(session_id))


def authorize_url(*, client_id: str, redirect_uri: str, state: str, verifier: str) -> str:
    """Section 4: ``show_dialog=true`` so a user signed in to another Spotify account sees the
    account chooser."""
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": challenge_of(verifier),
        "show_dialog": "true",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def scope_granted(granted: str) -> bool:
    """The token answer's ``scope`` holds both ``REQUIRED_SCOPES`` (a smaller grant is
    ``scope_mismatch``); ``playlist-modify-public`` is asked for but not required."""
    return set(REQUIRED_SCOPES) <= set(granted.split())
