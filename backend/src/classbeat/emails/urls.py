"""The structural link check (ADR 0022 section 2).

A link in a mail is accepted only if, parsed with ``urllib.parse``, it is on the app's own origin
(scheme, host and port equal to ``public_base_url``, no userinfo) or is a ``mailto:`` of exactly the
support address. A look-alike host (``classbeat.eu.attacker.example``), userinfo, another port or
scheme and ``javascript:`` all fail. A string prefix test would accept some of them.
"""

from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


class UnsafeLink(ValueError):
    """A link that does not belong in a mail. The message never includes the link."""


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme)


def check_link(url: str, *, base_url: str, support_address: str) -> str:
    """Return ``url`` unchanged if it is allowed, else raise ``UnsafeLink``."""
    if not url or any(c.isspace() or ord(c) < 32 or c in "\\<>\"'" for c in url):
        raise UnsafeLink("link has forbidden characters")
    if url.lower().startswith("mailto:"):
        if url[len("mailto:") :] != support_address:
            raise UnsafeLink("mailto link is not the support address")
        return url
    try:
        parts = urlsplit(url)
        origin = _origin(url)
    except ValueError:  # a bad port, an unbalanced bracket
        raise UnsafeLink("link does not parse") from None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UnsafeLink("link has userinfo")
    if not origin[1] or origin != _origin(base_url):
        raise UnsafeLink("link is not on the public base URL")
    return url
