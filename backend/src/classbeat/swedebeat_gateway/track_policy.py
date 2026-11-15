"""The track download's rules (ADR 0036 sections 3.4 and 8, SR C2). Pure: no I/O.

- **The reference** is the catalogue's id of a track: ``REFERENCE`` (``^[A-Za-z0-9_-]{1,64}$``),
  the gateway's own ``is_safe_reference`` shape. It is checked before it goes into a path
  (``download_path``) and when the match's answer is parsed (``match_policy``).
- **The one path** is ``GET /tracks/{reference}/download``, with no query: never the gateway's
  ``preview``. ``DOWNLOAD_PATH`` is the template; the guard allows exactly that string.
- **The answer** must be ``audio/mpeg`` (``is_audio_mpeg``: the media type only, parameters
  ignored) with identity encoding only (``is_identity``) and at most ``DOWNLOAD_MAX`` (60 MB) bytes,
  both as declared (``declared_length``) and as read.
- **The status** to a category (``download_category``): ADR 0035 section 5's table as the match
  has it (``status_policy.call_category``: ``401 unknown client`` and ``403`` are
  ``app_misconfigured``, another ``401`` is ``bearer_refused``, ``429`` ``rate_limited``, ``5xx``
  ``upstream_unavailable``, a ``3xx`` ``redirect``), plus the download's own: ``404`` (the
  catalogue has no file for that reference) is ``track_unavailable`` and ``413`` (or a body over
  the cap) is ``track_too_large``.
"""

import re
from typing import Final, Literal

from classbeat.swedebeat_gateway.status_policy import CallCategory, call_category

DOWNLOAD_PATH: Final = "/tracks/{reference}/download"
DOWNLOAD_MAX: Final = 60 * 1024 * 1024
AUDIO_MPEG: Final = "audio/mpeg"
REFERENCE: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")

DownloadCategory = CallCategory | Literal["track_unavailable", "track_too_large"]


def is_reference(value: object) -> bool:
    """``value`` is a catalogue reference: 1 to 64 ASCII letters, digits, ``_`` or ``-``."""
    return isinstance(value, str) and REFERENCE.fullmatch(value) is not None


def download_path(reference: str) -> str:
    """The download's path for ``reference``; ``ValueError`` for anything that is not one."""
    if not is_reference(reference):
        raise ValueError("a catalogue reference is 1 to 64 letters, digits, '_' or '-'")
    return DOWNLOAD_PATH.format(reference=reference)


def is_audio_mpeg(content_type: str | None) -> bool:
    """The answer's media type is ``audio/mpeg`` (any parameter after ``;`` is ignored)."""
    if content_type is None:
        return False
    return content_type.split(";", 1)[0].strip().lower() == AUDIO_MPEG


def is_identity(encodings: list[str]) -> bool:
    """No ``Content-Encoding`` but ``identity``, in every header line and list item."""
    for value in encodings:
        for token in value.split(","):
            if token.strip().lower() not in ("", "identity"):
                return False
    return True


def declared_length(value: str | None) -> int | None:
    """``Content-Length`` as a number, ``None`` when absent; ``ValueError`` when malformed."""
    if value is None:
        return None
    text = value.strip()
    if not text.isascii() or not text.isdigit() or len(text) > 15:
        raise ValueError("a malformed Content-Length")
    return int(text)


def download_category(status: int, *, unknown_client: bool) -> DownloadCategory | None:
    """``None`` for a ``200`` (its body is checked by the caller), else the category."""
    if status == 404:
        return "track_unavailable"
    if status == 413:
        return "track_too_large"
    return call_category(status, unknown_client=unknown_client, valid_body=True)
