"""The checks of the player's OpenID Connect client settings (ADR 0034 section 1). Pure: no I/O.

The client id is 1 to 128 letters, digits, ``.``, ``_`` or ``-``. The redirect address is an
absolute ``https`` URL (``http`` only on a loopback host, for local stacks) without user info or
a fragment, at most 512 characters; the authorize request must name it exactly.
"""

import re
from urllib.parse import urlsplit

_CLIENT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
REDIRECT_MAX = 512


def check_client_id(value: str | None) -> str | None:
    if value is not None and not _CLIENT_ID.fullmatch(value):
        raise ValueError("oidc_player_client_id must be 1 to 128 letters, digits, '.', '_' or '-'")
    return value


def check_redirect_uri(value: str | None) -> str | None:
    if value is None:
        return None
    problem = "oidc_player_redirect_uri must be an absolute https URL without a fragment"
    if len(value) > REDIRECT_MAX or not value.isprintable() or " " in value:
        raise ValueError(problem)
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        raise ValueError(problem) from None
    secure = parts.scheme == "https" or (parts.scheme == "http" and host in _LOOPBACK)
    if not secure or not host or parts.fragment or "#" in value or "@" in parts.netloc:
        raise ValueError(problem)
    return value
