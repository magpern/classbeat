"""The languages classbeat offers and the request locale (ADR 0023 sections 1, 2 and 9).

``SUPPORTED_LOCALES`` is the checked-in manifest ``frontend/src/i18n/locales.json`` (the image
carries a copy at ``/app/locales.json``); ``CLASSBEAT_LOCALES`` enables a subset, and a language is
*offered* when it is both. A locale is an allowlisted code everywhere: input is compared with the
offered set and otherwise dropped, never echoed, and never builds a path, header or query.
"""

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from classbeat.config import Settings

DEFAULT_LOCALE = "en"
CODE = re.compile(r"[a-z]{2}")
# Accept-Language is read from its first 256 bytes and at most 8 entries (ADR 0023 section 2), so
# an 8 KiB header with a thousand entries costs the same as a short one.
ACCEPT_LANGUAGE_BYTES = 256
ACCEPT_LANGUAGE_ENTRIES = 8
LOCALE_FIELD_MAX = 16  # a request's ``locale`` field: longer is certainly not a code


def _manifest_path() -> Path:
    here = Path(__file__).resolve()
    candidates = (
        here.parents[3] / "frontend" / "src" / "i18n" / "locales.json",  # the repository
        here.parents[2] / "locales.json",  # the image (/app/locales.json)
    )
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("the locale manifest locales.json was not found")


def _load() -> tuple[str, ...]:
    codes = json.loads(_manifest_path().read_text(encoding="utf-8"))
    if not isinstance(codes, list) or not all(
        isinstance(c, str) and CODE.fullmatch(c) for c in codes
    ):
        raise RuntimeError("locales.json must be a list of two-letter codes")
    return tuple(dict.fromkeys(codes))


SUPPORTED_LOCALES: tuple[str, ...] = _load()


def offered_locales(settings: "Settings") -> tuple[str, ...]:
    """Enabled and supported, in the manifest's order, ``en`` first when it is supported."""
    enabled = settings.enabled_locales
    return tuple(code for code in SUPPORTED_LOCALES if code in enabled)


def is_offered(settings: "Settings", value: object) -> bool:
    return isinstance(value, str) and value in offered_locales(settings)


def offered_value(settings: "Settings", value: object) -> str | None:
    """``value`` itself when it is an offered code, else ``None`` (typed as ``str``)."""
    return value if isinstance(value, str) and value in offered_locales(settings) else None


def parse_accept_language(header: str | None, offered: tuple[str, ...]) -> str | None:
    """The offered language the header ranks first, or ``None``.

    Bounded: only the first 256 characters are split, into at most 8 entries. Entries are compared
    exactly (``sv``, not ``sv-SE``); ``q=0`` excludes an entry, and ties keep the header's order.
    """
    if not header:
        return None
    best: tuple[float, int, str] | None = None
    entries = header[:ACCEPT_LANGUAGE_BYTES].split(",", ACCEPT_LANGUAGE_ENTRIES)
    for position, entry in enumerate(entries[:ACCEPT_LANGUAGE_ENTRIES]):
        tag, _, params = entry.partition(";")
        code = tag.strip().lower()
        if code not in offered:
            continue
        quality = 1.0
        for param in params.split(";")[:2]:
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
        if not 0.0 < quality <= 1.0:
            continue
        if best is None or quality > best[0]:
            best = (quality, position, code)
    return None if best is None else best[2]


def request_locale(settings: "Settings", field: object, accept_language: str | None) -> str:
    """The language of a request (ADR 0023 section 2): the body's ``locale`` field if offered,
    else the ``Accept-Language`` header, else English. Always an offered code (or ``en``)."""
    offered = offered_locales(settings)
    if isinstance(field, str) and len(field) <= LOCALE_FIELD_MAX and field in offered:
        return field
    return parse_accept_language(accept_language, offered) or DEFAULT_LOCALE


def storable_locale(settings: "Settings", locale: str) -> str | None:
    """What a new account stores. With a single offered language every account would be pinned to
    it once more arrive, so ``NULL`` (follow the browser) is stored until a second one is offered
    (issue #664, from the ML0 review)."""
    return locale if len(offered_locales(settings)) > 1 else None
