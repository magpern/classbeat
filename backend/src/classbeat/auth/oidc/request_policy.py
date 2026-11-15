"""Validating an authorization request (ADR 0034 section 1). Pure: no I/O.

``check_authorize`` takes the query parameters and the one registered client and answers one of:

- ``ErrorPage``: the client or the redirect address is wrong (or repeated). The caller shows an
  error page and **never redirects**: an unregistered address must not receive anything.
- ``RedirectError``: anything else is wrong. The caller redirects to the *registered* address
  with ``error`` (``invalid_request``) and the request's ``state`` when it has a usable one.
- ``AuthorizeRequest``: everything the flow stores.

``prompt`` may be absent or ``login``; ``none``, ``consent``, ``select_account`` (alone or in a
list) and any ``max_age`` are ``invalid_request``, since the player never sends them. ``scope``
must hold ``openid`` and nothing but ``openid`` and ``email``. Every value is limited in length
and to printable ASCII (``scope``: words separated by single spaces), and a parameter given
twice is refused.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

VALUE_MAX = 512
ALLOWED_SCOPES = frozenset({"openid", "email"})
# RFC 7636: 43 to 128 characters of the unreserved set; S256 makes it exactly 43.
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
_PRINTABLE = re.compile(r"[\x21-\x7e]+")
_SCOPE = re.compile(r"[\x21-\x7e]+(?: [\x21-\x7e]+)*")  # space-separated (RFC 6749 3.3)
_REQUIRED = ("response_type", "scope", "state", "nonce", "code_challenge", "code_challenge_method")


@dataclass(frozen=True, slots=True)
class Client:
    client_id: str
    redirect_uri: str


@dataclass(frozen=True, slots=True)
class ErrorPage:
    reason: str  # "client" or "redirect_uri", for the test and the event; never shown


@dataclass(frozen=True, slots=True)
class RedirectError:
    error: str
    state: str | None


@dataclass(frozen=True, slots=True)
class AuthorizeRequest:
    redirect_uri: str
    state: str
    nonce: str
    code_challenge: str
    prompt_login: bool


Outcome = ErrorPage | RedirectError | AuthorizeRequest


def _single(params: Mapping[str, Sequence[str]], name: str) -> str | None:
    """The one value of ``name``; ``None`` when absent, ``""`` when repeated or unusable."""
    values = params.get(name, ())
    if not values:
        return None
    shape = _SCOPE if name == "scope" else _PRINTABLE
    if len(values) != 1 or len(values[0]) > VALUE_MAX or not shape.fullmatch(values[0]):
        return ""
    return values[0]


def check_authorize(params: Mapping[str, Sequence[str]], client: Client) -> Outcome:
    if _single(params, "client_id") != client.client_id:
        return ErrorPage("client")
    if _single(params, "redirect_uri") != client.redirect_uri:
        return ErrorPage("redirect_uri")
    state = _single(params, "state") or None
    invalid = RedirectError("invalid_request", state)
    values = {name: _single(params, name) for name in _REQUIRED}
    if any(not value for value in values.values()):
        return invalid
    if values["response_type"] != "code" or values["code_challenge_method"] != "S256":
        return invalid
    scopes = (values["scope"] or "").split(" ")
    if "openid" not in scopes or not set(scopes) <= ALLOWED_SCOPES:
        return invalid
    if not _CHALLENGE.fullmatch(values["code_challenge"] or ""):
        return invalid
    if "max_age" in params:
        return invalid
    prompt = _single(params, "prompt")
    if prompt not in (None, "login"):
        return invalid
    return AuthorizeRequest(
        redirect_uri=client.redirect_uri,
        state=values["state"] or "",
        nonce=values["nonce"] or "",
        code_challenge=values["code_challenge"] or "",
        prompt_login=prompt == "login",
    )
